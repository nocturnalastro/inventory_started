import itertools
import tempfile
import typing
from asyncio import subprocess
from dataclasses import dataclass

from ruamel import yaml

from . import parts
from .inventory import Inventory


@dataclass
class Question:
    text: str
    field: str = ""
    default: typing.Optional[typing.Any] = None
    allow_default_none: bool = False

    @property
    def allow_default(self):
        return self.default is not None or self.allow_default_none

    def __iter__(self):
        yield self


@dataclass
class ListQuestion:
    delimeter: str = ","


class Questionaire:
    def __init__(self):
        self.inventory = Inventory()

    def run(self):

        self._is_sno = self._yes_or_no_bool(Question("Do you want to deploy SNO"))
        config = self.prepare_crucible_config()
        self.prepare_cluster_definition()
        if config["setup_dns_service"]:
            self.prepare_dns_service()
        if config["setup_http_store_service"]:
            self.prepare_http_store_service()
        if config["setup_registry_service"]:
            self.prepare_registry_service()
        if config["setup_assisted_installer"]:
            self.prepare_assisted_installer()
        self.prepare_vm_host_hosts()
        self.prepare_nodes()

    @staticmethod
    def _output(text, end="\n"):
        print(text, end=end)

    @staticmethod
    def _input(text):
        return input(f"{text}: ").strip()

    def _ask(self, question: Question):
        answer = self._input(question.text)
        if question.allow_default and (answer is None or answer == ""):
            return question.default
        return answer

    def _yes_or_no_bool(self, question: Question):
        answer = ""
        while answer.lower() not in ["n", "no", "y", "yes"]:
            answer = self._ask(question)
        return answer.lower() in ["y", "yes"]

    def _yes_or_no_field(self, question: Question):
        return {question.field: self._yes_or_no_bool(question)}

    def _matches_type(self, question, filed_type):
        answer = None
        while answer is None:
            answer_candidate = self._ask(question)
            _union = (typing.Union, typing.types.UnionType)
            if typing.get_origin(filed_type) in _union:
                exceptions = []
                for sub_type in typing.get_args(filed_type):
                    try:
                        answer = sub_type(answer_candidate)
                        break
                    except Exception as e:
                        exceptions.append(str(e))
                if len(exceptions) > 0:
                    exc_str = "\n".join(exceptions)
                    self._output(f"Not able to find correct type:\n{exc_str}")
            elif typing.get_origin(filed_type) is list and isinstance(
                question, ListQuestion
            ):
                exceptions = []
                values = []
                continue_evaluating = True
                for answer_part in answer_candidate.split(question.delimeter):
                    for sub_type in typing.get_args(filed_type):
                        try:
                            values.append(sub_type(answer_part))
                            break
                        except Exception as e:
                            exceptions.append(str(e))
                        if len(exceptions) > 0:
                            continue_evaluating = False
                            break
                    if not continue_evaluating:
                        break
                if len(exceptions) > 0:
                    exc_str = "\n".join(exceptions)
                    self._output(f"Not able to find correct type:\n{exc_str}")
            else:
                try:
                    answer = filed_type(answer_candidate)
                except Exception as e:
                    self._output(e)
        return answer

    def _prepare_using_types_and_questions(self, questions, host_cls):
        values = {}
        cls_types = typing.get_type_hints(host_cls)
        for question in questions:
            values[question.field] = self._matches_type(
                question,
                cls_types.get(question.field, str),
            )
        return values

    def _prepare_host(self, host_cls, name=None):
        values = {}
        questions = [
            Question(
                field="ansible_host",
                text="What is the hosts ip address",
            ),
        ]
        if name is None:
            questions = [
                Question(field="name", text="What is the name of the host")
            ] + questions
        else:
            values = {"name": name}

        values.update(self._prepare_using_types_and_questions(questions, host_cls))
        return values

    def _prepare_vm_host_networking(self):
        if self._yes_or_no_bool(
            Question(
                text="Do you wish to use an nmstate network for vm_host [y/N]",
                default="no",
            )
        ):
            with tempfile.NamedTemporaryFile() as tmpfile:
                EDITOR = "${EDITOR:-vi}"
                subprocess.run(f"{EDITOR} {tmpfile.name}", shell=True)
                network_config_content = tmpfile.read()
                # TODO: allow for blank or all comments then re-ask.
                # TODO: add some validation here for now assume its good.
                nc_values = yaml.load_all(network_config_content, yaml.SafeLoader)
                # This will allow for some templating by allowing them to define extra values
                if "network_config" in network_config_content:
                    values.update(**nc_values)
                else:
                    values["network_config"] = network_config_content
        else:
            questions = [
                Question(
                    field="vm_bridge_ip",
                    text="What is the expected IP address of the VM bridge",
                ),
                Question(
                    field="vm_bridge_interface",
                    text="Which interface do you wish the bridge to connect to",
                ),
                Question(field="dns", text="Which dns server should the bridge use"),
            ]
            values = self._prepare_using_types_and_questions(questions, parts.VMHost)
            if self._yes_or_no_bool(
                Question(
                    text="Do you want to add a vlan tag to your bridge [y/N]",
                    default="no",
                )
            ):
                values.update(
                    self._prepare_using_types_and_questions(
                        Question(field="vm_vlan_tag", text="What is that vlan tag"),
                        parts.VMHost,
                    )
                )
            return values

    def prepare_vm_host(self):
        values = {}
        self._output("VM Host:")
        values.update(self._prepare_host(parts.VMHost))
        # TODO: Ask for if they want to setup host networking
        values.update(self._prepare_vm_host_networking())
        self.inventory.vm_hosts.add_host(parts.VMHost(**values))
        return values

    def prepare_vm_host_hosts(self):
        values = {}
        if self._yes_or_no_bool(
            Question(
                text="Do you want crucible to prepare KVM nodes for you?[y/N]",
                default="no",
            )
        ):
            _values = self.prepare_vm_host()
            values["vm_hosts"] = {_values["name"]: _values}
            while self._yes_or_no_bool(
                Question(
                    text="Would you like to add another VM Host?[y/N]",
                    default="no",
                )
            ):
                _values = self.prepare_vm_host()
                values["vm_hosts"].update({_values["name"]: _values})

    def prepare_crucible_config(self):
        values = {}
        values.update(
            self._prepare_using_types_and_questions(
                Question(
                    field="repo_root_path",
                    text="What is the path to the crucible dir",
                ),
                parts.CrucibleConfig,
            )
        )
        for question in [
            Question(
                field="setup_ntp_service",
                text="Do you want crucible to setup a NTP server [Y/n]",
                default="yes",
            ),
            Question(
                field="setup_http_store_service",
                text="Do you want crucible to setup a HTTP Server [Y/n]",
                default="yes",
            ),
            Question(
                field="setup_dns_service",
                text="Do you want crucible to setup a DNS (or DHCP) Server [Y/n]",
                default="yes",
            ),
            Question(
                field="setup_registry_service",
                text="Do you want crucible to setup a local container registry [Y/n]",
                default="yes",
            ),
            Question(
                field="setup_assisted_installer",
                text="Do you want crucible to setup a local assisted installer service [Y/n]",
                default="yes",
            ),
            # fetched_dest
            # pull_secret_lookup_paths
            # ssh_public_key_lookup_paths
            # ssh_key_dest_base_dir
            # kubeconfig_dest_dir
            # kubeconfig_dest_filename
        ]:
            values.update(self._yes_or_no_field(question))

        self.inventory.all_section.add_part(
            "crucible_config",
            parts.CrucibleConfig(**values),
        )
        return values

    def prepare_ntp_server(self, cluster_def_values=None):
        values = {}
        self._output("NTP Sever:")
        values.update(self._prepare_host(parts.services.NTPHost, name="ntp_host"))

        if cluster_def_values is not None:
            values["ntp_server_allow"] = cluster_def_values["machine_network_cidr"]
        elif cluser_def := self.inventory.all_section.parts.get("cluster_definition"):
            values["ntp_server_allow"] = cluser_def.machine_network_cidr
        else:
            values.update(
                self._prepare_using_types_and_questions(
                    Question(
                        field="ntp_server_allow",
                        text="What network are NTP clients on",
                    ),
                    parts.services.NTPHost,
                )
            )
        self.inventory.services.add_host(parts.services.NTPHost(**values))
        return values

    def prepare_cluster_definition(self):
        values = {}
        cls_types = typing.get_type_hints(parts.ClusterDefinition)
        openshift_versions = cls_types["openshift_full_version"]
        network_types = cls_types["network_type"]
        questions = [
            Question(field="cluster_name", text="Cluster name"),
            Question(field="base_dns_domain", text="Base dns domain"),
            Question(
                field="openshift_full_version",
                text=(
                    "Which openshift version do you want to deploy "
                    f"[{','.join(x.value for x in openshift_versions)}]"
                ),
            ),
        ]

        if self._is_sno:
            questions += [
                Question(field="api_vip", text="API VIP"),
                Question(field="ingress_vip", text="Ingress VIP"),
            ]
        else:
            node_values = self._prepare_node(self, role="master")
            values.update(
                api_vip=node_values["ansible_host"],
                ingress_vip=node_values["ansible_host"],
            )

        questions += [
            Question(field="machine_network_cidr", text="Machine network CIDR"),
            Question(field="service_network_cidr", text="Service Network CIDR"),
            Question(
                field="cluster_network_cidr",
                text="Internal cluster network CIDR",
            ),
            Question(
                field="cluster_network_host_prefix",
                text="Internal cluster network host prefix",
            ),
            Question(
                field="network_type",
                text=f"Network type [{','.join(x.value for x in network_types)}]",
            ),
        ]

        values.update(
            self._prepare_using_types_and_questions(
                questions,
                parts.ClusterDefinition,
            )
        )

        config = self.inventory.all_section.parts.get("crucible_config")

        if config is None or config.setup_ntp_service is False:
            values.update(
                self._prepare_using_types_and_questions(
                    Question(field="ntp_server", text="NTP Server address"),
                    parts.ClusterDefinition,
                )
            )
        else:
            ntp_server_values = self.prepare_ntp_server(cluster_def_values=values)
            values["ntp_server"] = ntp_server_values["ansible_host"]
        self.inventory.all_section.add_part(
            "cluster_definition",
            parts.ClusterDefinition(**values),
        )
        return values

    def prepare_dns_service(self, cluster_def_values=None):
        values = {}
        self._output("DNS/DHCP Host:")
        values.update(
            self._prepare_host(
                name="dns_host",
                host_cls=parts.services.DNSHost,
            )
        )
        if self._yes_or_no_bool(
            Question(
                text="Is there an upstream dns server you wish query [y/N]",
                default="no",
            )
        ):
            values.update(
                self._prepare_using_types_and_questions(
                    Question(
                        field="upstream_dns",
                        text="Upstream dns IP address",
                    ),
                    parts.services.DNSHost,
                )
            )

        if (
            update := self._yes_or_no_field(
                Question(
                    field="use_dhcp",
                    text="Do you want dhcp [y/N]",
                    default="no",
                )
            )
        )["use_dhcp"]:

            values.update(update)

            questions = [
                Question(field="dhcp_range_first", text="First IP in DHCP range"),
                Question(field="dhcp_range_last", text="Last IP in DHCP range"),
                Question(field="gateway", text="Network gatewaty"),
            ]

            inventory = self.inventory
            if cluster_def_values is not None:
                values["prefix"] = cluster_def_values["machine_network_cidr"].prefixlen
            elif cluser_def := inventory.all_section.parts.get("cluster_definition"):
                values["prefix"] = cluser_def.machine_network_cidr.prefixlen
            else:
                questions = [
                    Question(field="prefix", text="The network host prefix length")
                ] + questions

            values.update(
                self._prepare_using_types_and_questions(
                    questions, parts.services.DNSHost
                )
            )

        if not self._yes_or_no_bool(
            Question(
                text="Can you use virtual media with your nodes [Y/n]",
                default="yes",
            )
        ):
            values["use_pxe"] = True
            self.prepare_tftp_host(dhcp_values=values)
        else:
            values["use_pxe"] = False

        self.inventory
        return values

    def prepare_http_store_service(self):
        values = {}
        self._output("HTTP Store host:")
        values.update(
            self._prepare_host(
                name="http_store",
                host_cls=parts.services.HTTPStore,
            )
        )
        self.inventory.services.add_host(parts.services.HTTPStore(**values))
        return values

    def prepare_registry_service(self):
        values = {}
        self._output("Registry host:")
        values.update(
            self._prepare_host(
                name="registry_host",
                host_cls=parts.services.RegistryHost,
            )
        )

        questions = []
        if not self._yes_or_no_bool(
            Question(text="Does the hostname matcht he dns entry for the registry host")
        ):
            questions.append(
                Question(
                    field="registry_fqdn",
                    text="What is the domain name you wish to access the machine",
                )
            )
        questions += [
            Question(field="cert_country", text="Cert. country"),
            Question(field="cert_locality", text="Cert. locality"),
            Question(field="cert_organization", text="Cert. org."),
            Question(field="cert_organizational_unit", text="Cert. org. unit"),
            Question(field="cert_state", text="Cert. state"),
        ]

        values.update(
            self._prepare_using_types_and_questions(
                questions,
                parts.services.RegistryHost,
            )
        )
        self.inventory.services.add_host(parts.services.RegistryHost(**values))
        return values

    def prepare_assisted_installer(self):
        values = {}
        self._output("Assisted Installer host:")
        values.update(
            self._prepare_host(
                name="assisted_installer",
                host_cls=parts.services.AssistedInstaller,
            )
        )

        questions = [Question(field="host", text="Base address")]

        if dns_def := self.inventory.services.hosts.get("dns_host"):
            values["dns_servers"] = [dns_def.ansible_host]
        else:
            config = self.inventory.all_section.parts.get("crucible_config")
            if config is None or config.setup_dns_service is False:
                questions.append(
                    ListQuestion(
                        field="dns_servers",
                        text="DNS servers for pod (serperated by ',')",
                        delimeter=",",
                    )
                )

        values.update(
            self._prepare_using_types_and_questions(
                questions,
                parts.services.AssistedInstaller,
            )
        )
        self.inventory.services.add_host(parts.services.AssistedInstaller(**values))
        return values

    def prepare_tftp_host(self, dhcp_values=None):
        values = {"name": "tftp_host"}

        if dhcp_values is not None:
            values["ansible_host"] = dhcp_values["ansible_host"]
        elif dhcp_def := self.inventory.services.hosts.get("dns_host"):
            values["ansible_host"] = dhcp_def.ansible_host
        else:
            self._output("TFTP host:")
            values.update(
                self._prepare_host(
                    name="tftp_host",
                    host_cls=parts.services.TFTPHost,
                )
            )

        self.inventory.services.add_host(parts.services.TFTPHost(**values))
        return values

    def _prepare_node(self, host_cls=None, role: parts.node.Roles = None):
        values = {}
        self._output("Node:")

        if host_cls is None and self._yes_or_no_bool(
            Question(text="Is the node a VM [y/N]", default="no")
        ):
            host_cls = parts.node.VMNode
        else:
            host_cls = parts.node.Node

        values.update(self._prepare_host(host_cls=host_cls))

        if role is not None:
            values["role"] = parts.node.Roles.master

        values.update(
            self._prepare_using_types_and_questions(
                [
                    Question(field="bmc_address", text="BMC Address"),
                    Question(field="bmc_user", text="BMC user"),
                    Question(field="bmc_password", text="BMC password"),
                    Question(field="mac", text="Mac address to identify node"),
                ],
                host_cls=host_cls,
            ),
        )

        if host_cls is parts.node.Node:
            values.update(
                self._prepare_using_types_and_questions(
                    Question(
                        field="vendor",
                        text=f"Vendor [{','.join(x.value for x in parts.node.Vendors)}]",
                    ),
                    host_cls=host_cls,
                )
            )
        else:
            values.update(
                self._prepare_using_types_and_questions(
                    Question(field="vm_host", text=f"VM Host"),
                    host_cls=host_cls,
                )
            )

            if not self._yes_or_no_bool(
                text="Do you want to use the defualt vm_spec [Y/n]",
                default="yes",
            ):
                values["vm_spec"] = parts.node.VMSpec(
                    **self._prepare_using_types_and_questions(
                        [],
                        parts.node.VMSpec,
                    )
                )

        self.inventory.nodes.children.groups[values["role"]].add_host(
            host_cls(**values)
        )
        return values

    def prepare_nodes(self):
        if self._is_sno:
            # TODO: Check if any VMHosts ...
            return [self._prepare_node(role=parts.node.Roles.master)]

        nodes = []
        while len(nodes) == 0 or self._yes_or_no_bool(
            Question(text="Would you like to add another node [y/N]", default="no")
        ):
            self._prepare_node()
