"""Imports helper and an aliased export; also references an ambiguous name."""

from util import helper as do_help
from util import shared_name as util_shared


def run():
    value = do_help()
    # Ambiguous unqualified reference (two shared_name exports in the repo).
    return shared_name()
