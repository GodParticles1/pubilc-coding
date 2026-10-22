"""Explicit test-only host adapter. Never installed in a runtime release.

Production rejects fixture mode. Only this driver may replace OS/mount identity
checks for disposable temp roots while executing the unchanged transaction.
No environment variable or marker activates this adapter in production.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


def install_fixture(gm):
    if getattr(gm, "_tests_fixture_installed", False):
        return
    original_parser = gm.parser
    original_profile = gm.target_profile

    def parser():
        p = original_parser()
        for action in p._subparsers._group_actions:
            for op in ("plan", "apply", "recover"):
                for option in action.choices[op]._actions:
                    if option.dest == "target_mode":
                        option.choices = (*option.choices, "fixture")
        parse = p.parse_args

        def parse_args(argv=None, namespace=None):
            args = parse(argv, namespace)
            if getattr(args, "target_mode", None) == "fixture":
                args.target_mode = "offline"
                args._tests_fixture = True
            return args

        p.parse_args = parse_args
        return p

    def target_profile(args):
        if not getattr(args, "_tests_fixture", False):
            return original_profile(args)
        root = gm.no_symlink_ancestors(args.target_root, True).resolve()
        # A marker alone is insufficient: only explicit tests may call this,
        # and every mutable root must be under the OS disposable temp directory.
        temp = Path(tempfile.gettempdir()).resolve()
        gm.require(root != Path(root.anchor) and temp in root.parents,
                   "fixture cannot target running root or a non-temporary directory")
        marker = root / ".green-v11-fixture"
        gm.require(marker.is_file() and not marker.is_symlink(), "fixture profile missing")
        data = json.loads(marker.read_text(encoding="utf-8"))
        gm.require(data.get("target_id") == args.target_id, "wrong target identity")
        return root, data

    gm.parser = parser
    gm.target_profile = target_profile
    gm._tests_fixture_installed = True


def load_image():
    path = Path(__file__).resolve().parents[1] / "green_v11_image.py"
    spec = importlib.util.spec_from_file_location("green_image_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    install_fixture(module)
    return module


if __name__ == "__main__":
    sys.exit(load_image().main())
