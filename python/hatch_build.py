"""Bundle shared policy assets in both checkout and source-distribution builds."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        # An extracted sdist carries its own assets; it must not depend on the
        # original repository or an unrelated sibling directory named shared.
        assets = root / "src" / "judge_jev" / "assets"
        bundled = assets.is_dir()
        if not bundled:
            assets = root.parent / "shared"
        destination = "src/judge_jev/assets" if self.target_name == "sdist" else "judge_jev/assets"
        includes = build_data.setdefault("force_include", {})
        for name in ("rubrics", "schemas"):
            source = assets / name
            if not source.is_dir():
                raise FileNotFoundError(f"Missing judge-jev assets: {source}")
            # Package-local assets are already selected by Hatch's normal walk.
            if not bundled:
                includes[str(source)] = f"{destination}/{name}"
