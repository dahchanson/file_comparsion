"""JSON renderer for diff and dupes output."""

import json
import sys

from fcompare.display.renderer import ShowFilter
from fcompare.engine.models import DiffResult, DupeGroup


def render_diff_json(result: DiffResult, show: ShowFilter) -> None:
    """Print a DiffResult as a JSON object to stdout."""
    output: dict[str, object] = {
        "left": str(result.left_root),
        "right": str(result.right_root),
        "summary": {
            "total": result.total_files,
            "identical": len(result.identical),
            "modified": len(result.modified),
            "only_left": len(result.only_in_left),
            "only_right": len(result.only_in_right),
        },
    }

    if show in (ShowFilter.all, ShowFilter.modified):
        output["modified"] = [
            {
                "rel_path": str(left.rel_path),
                "left_sha256": left.sha256,
                "right_sha256": right.sha256,
            }
            for left, right in result.modified
        ]

    if show in (ShowFilter.all, ShowFilter.unique):
        output["only_left"] = [str(r.rel_path) for r in result.only_in_left]
        output["only_right"] = [str(r.rel_path) for r in result.only_in_right]

    if show in (ShowFilter.all, ShowFilter.same):
        output["identical"] = [str(left.rel_path) for left, _ in result.identical]

    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


def render_dupes_json(groups: list[DupeGroup]) -> None:
    """Print duplicate groups as a JSON object to stdout."""
    total_redundant = sum(len(g.files) - 1 for g in groups)
    total_wasted = sum(g.wasted_bytes for g in groups)

    output = {
        "summary": {
            "groups": len(groups),
            "redundant_files": total_redundant,
            "reclaimable_bytes": total_wasted,
        },
        "groups": [
            {
                "sha256": g.sha256,
                "size": g.files[0].size if g.files else 0,
                "files": [str(r.path) for r in g.files],
            }
            for g in groups
        ],
    }

    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
