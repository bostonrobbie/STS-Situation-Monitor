#!/usr/bin/env python3
"""Back up the STS-Situation-Monitor Git repository.

Creates a Git bundle file (a complete, portable backup of the repo including
all branches, tags, and history) in the backups/ directory.

Usage:
    python scripts/repo_backup.py                  # bundle only
    python scripts/repo_backup.py --verify         # bundle + verify integrity
    python scripts/repo_backup.py --mirror URL     # also push a mirror to URL
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


BACKUP_DIR = Path("backups")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def git_bundle(backup_dir: Path) -> Path:
    """Create a git bundle containing all refs."""
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Get short commit hash for the filename
    result = run(["git", "rev-parse", "--short", "HEAD"])
    short_hash = result.stdout.strip() if result.returncode == 0 else "unknown"

    bundle_name = f"sts-monitor_{timestamp}_{short_hash}.bundle"
    bundle_path = backup_dir / bundle_name

    print(f"Creating git bundle: {bundle_path}")
    result = run(["git", "bundle", "create", str(bundle_path), "--all"])
    if result.returncode != 0:
        print(f"ERROR: git bundle failed:\n{result.stderr}", file=sys.stderr)
        raise SystemExit(1)

    size_mb = bundle_path.stat().st_size / (1024 * 1024)
    print(f"Bundle created: {bundle_path} ({size_mb:.1f} MB)")
    return bundle_path


def verify_bundle(bundle_path: Path) -> bool:
    """Verify a git bundle is valid and complete."""
    print(f"Verifying bundle: {bundle_path}")
    result = run(["git", "bundle", "verify", str(bundle_path)])
    if result.returncode != 0:
        print(f"VERIFICATION FAILED:\n{result.stderr}", file=sys.stderr)
        return False

    # Show what's in the bundle
    result = run(["git", "bundle", "list-heads", str(bundle_path)])
    ref_count = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0
    print(f"Bundle verified OK — contains {ref_count} refs")
    return True


def push_mirror(url: str) -> None:
    """Push a complete mirror to a remote URL."""
    print(f"Pushing mirror to: {url}")
    result = run(["git", "push", "--mirror", url])
    if result.returncode != 0:
        print(f"ERROR: mirror push failed:\n{result.stderr}", file=sys.stderr)
        raise SystemExit(1)
    print("Mirror push complete")


def cleanup_old_bundles(backup_dir: Path, keep: int = 5) -> None:
    """Keep only the N most recent bundle files."""
    bundles = sorted(backup_dir.glob("sts-monitor_*.bundle"), reverse=True)
    for old in bundles[keep:]:
        print(f"Removing old bundle: {old.name}")
        old.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up the STS repository")
    parser.add_argument("--verify", action="store_true", help="Verify bundle integrity")
    parser.add_argument("--mirror", metavar="URL", help="Also push a mirror to this remote URL")
    parser.add_argument("--keep", type=int, default=5, help="Number of old bundles to keep (default: 5)")
    args = parser.parse_args()

    # Check we're in a git repo
    result = run(["git", "rev-parse", "--git-dir"])
    if result.returncode != 0:
        print("ERROR: not in a git repository", file=sys.stderr)
        raise SystemExit(1)

    # Create bundle
    bundle_path = git_bundle(BACKUP_DIR)

    # Verify if requested
    if args.verify:
        if not verify_bundle(bundle_path):
            raise SystemExit(1)

    # Push mirror if requested
    if args.mirror:
        push_mirror(args.mirror)

    # Cleanup old bundles
    cleanup_old_bundles(BACKUP_DIR, keep=args.keep)

    print("\nBackup complete.")
    print(f"  Bundle: {bundle_path}")
    print(f"  Restore: git clone {bundle_path} sts-monitor-restored")


if __name__ == "__main__":
    main()
