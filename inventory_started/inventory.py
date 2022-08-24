from __future__ import annotations
from collections import defaultdict
from functools import lru_cache
from tokenize import group

from ruamel import yaml

from .parts.base import ValidationBase


class CanNotInsertInvalidValue(Exception):
    @classmethod
    def new(cls, type_, value):
        return cls(f"Can not insert {type_} {value}")


class Group(ValidationBase):
    def __init__(self, vars=None, hosts=None, children=None) -> None:
        self.vars = vars or []
        self.hosts = hosts or {}
        self.children: GroupList = children or GroupList()
        super().__init__()

    def validate(self, inventory: Inventory):
        for var_secton in vars:
            var_secton.validate(inventory)

        for host in self.hosts.values():
            host.validate(inventory)

        for child in self.children.values():
            child.validate(inventory)

    def add_var_section(self, section, validate=True):
        if validate and not section.validate(self):
            raise CanNotInsertInvalidValue.new("var_section", section)
        self.vars.append(section)

    def add_host(self, host, validate=True):
        if validate and not host.validate(self):
            raise CanNotInsertInvalidValue.new("host", host)
        self.hosts[host.name] = host

    def add_child(self, group: Group):
        self.children[group.name] = group

    def __len__(self):
        return len(self.hosts) + sum(len(child) for child in self.children)


class GroupList:
    def __init__(self, groups=None) -> None:
        self.groups: dict[str, Group] = defaultdict(Group)


class NodeGroup(Group):
    def validate(self, inventory: Inventory):
        if not ((self.children["masters"]) == 1 or ((self.children["masters"]) >= 3)):
            return False
        return super().validate(inventory)


class VarsSection(ValidationBase):
    def __init__(self, required=None) -> None:
        self.required = required or []
        self.parts = {}
        super().__init__()

    def add_part(self, name, part):
        self.parts[name] = part


class Inventory(ValidationBase):
    def __init__(
        self,
        all_section: VarsSection = None,
        bastions: Group = None,
        services: Group = None,
        vm_hosts: Group = None,
        nodes: NodeGroup = None,
    ) -> None:
        self.all_section = all_section or VarsSection()
        self.bastions = bastions or Group()
        self.services = services or Group()
        self.vm_hosts = vm_hosts or Group()
        self.nodes = nodes or NodeGroup()
        super().__init__()

    def validate(self):
        with self._validation_context:
            return all(
                x.vaildate(self)
                for x in (
                    self.all_section,
                    self.bastions,
                    self.services,
                    self.vm_hosts,
                    self.nodes,
                )
            )


class InventoryExporter:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory

    def export(self, func=yaml.dump):
        return func(self._asdict)

    @property
    def _asdict(self):
        groups = {
            "bastions": self._bastions,
            "services": self._services,
        }
        if len(vm_hosts := self._vm_hosts) > 0:
            groups["vm_hosts"] = vm_hosts

        group["nodes"] = self._nodes
        return {
            "all": {
                "vars": self._all_vars,
                "children": groups,
            }
        }

    @property
    def _all_vars(self):
        res = {}
        for part in self.inventory.all_section.parts:
            res.update(part.asdict())
        return res

    @property
    def _nodes(self):
        nodes_by_group = self._nodes_by_group
        node_groups = {
            "masters": {
                "hosts": nodes_by_group["master"],
            }
        }

        if len(nodes_by_group["worker"]) > 0:
            node_groups["workers"] = {
                "hosts": nodes_by_group["worker"],
            }
        return {"children": node_groups}

    @property
    def _nodes_by_group(self):
        res = {"masters": {}, "workers": {}}
        for node in self.inventory.nodes.hosts:
            res[node.role.value][node.name] = node.asdict()
        return res

    def _get_host_from_group(self, group):
        return {"hosts": {service.name: service.asdict() for service in group.hosts}}

    @property
    def _services(self):
        return self._get_host_from_group(self.inventory.services)

    @property
    def _vm_hosts(self):
        return self._get_host_from_group(self.inventory.vm_hosts)

    @property
    def _bastions(self):
        return self._get_host_from_group(self.inventory.vm_hosts)
