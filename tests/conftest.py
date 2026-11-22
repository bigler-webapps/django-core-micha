import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")


def pytest_addoption(parser):
    parser.addoption("--dcm-disposal-proof-handles", default=None,
                     help="JSON file with two approved disposable instance IDs; enables the Docker proof")
