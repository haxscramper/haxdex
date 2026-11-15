#!/usr/bin/env python
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import json
import tomllib
from pathlib import Path


def main():
    lock = tomllib.loads(Path("uv.lock").read_text())
    packages = lock["package"]

    members = set(lock.get("manifest", {}).get("members", []))
    if not members:
        members = {
            package["name"]
            for package in packages
            if package.get("source", {}).get("editable") == "."
            or package.get("source", {}).get("virtual") == "."
        }

    members.difference_update(
        package["name"]
        for package in packages
        if package.get("source", {}).get("editable") == "."
        or package.get("source", {}).get("virtual") == "."
    )

    edges = set()
    for package in packages:
        name = package["name"]
        if name not in members:
            continue

        dependencies = list(package.get("dependencies", []))
        for section in ("optional-dependencies", "dev-dependencies"):
            for group in package.get(section, {}).values():
                dependencies.extend(group)

        for dependency in dependencies:
            target = dependency["name"]
            if target in members:
                edges.add((name, target))

    print("digraph workspace {")
    print('  rankdir = "LR";')
    print('  node [shape = "box"];')

    for name in sorted(members):
        print(f"  {json.dumps(name)};")

    for source, target in sorted(edges):
        print(f"  {json.dumps(source)} -> {json.dumps(target)};")

    print("}")


if __name__ == "__main__":
    main()
