from inventory_started import __version__
from inventory_started.main import Questionaire


def test_version():
    assert __version__ == "0.1.0"


def test_questionare():
    Questionaire().run()
