"""Migrate agent workspace files between storage backends.

This script supports bidirectional migration between local and S3-compatible
storage backends. It can migrate all files or filter by agent ID or
enterprise knowledge base.
"""

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

# Fix path for running as module
if TYPE_CHECKING:
    pass
else:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import get_settings
from app.services.storage.interface import FileInfo, StorageBackend, StorageError
from app.services.storage.local import LocalStorageBackend
from app.services.storage.s3 import S3StorageBackend


def _create_backend(
    backend_type: str,
    bucket: str = "",
    endpoint_url: str = "",
    region: str = "",
    access_key: str = "",
    secret_key: str = "",
    agent_data_dir: str = "",
) -> StorageBackend:
    """Create a storage backend instance from configuration."""
    backend_type = backend_type.lower().strip()

    if backend_type == "local":
        if not agent_data_dir:
            settings = get_settings()
            agent_data_dir = settings.AGENT_DATA_DIR
        return LocalStorageBackend(root_dir=agent_data_dir)

    if backend_type == "s3":
        settings = get_settings()
        endpoint = endpoint_url or settings.STORAGE_ENDPOINT_URL
        # Auto-detect force_path_style for non-AWS endpoints
        force_path_style = not (endpoint and "amazonaws.com" in endpoint)
        return S3StorageBackend(
            bucket=bucket or settings.STORAGE_BUCKET,
            endpoint_url=endpoint,
            region=region or settings.STORAGE_REGION,
            access_key=access_key or settings.STORAGE_ACCESS_KEY,
            secret_key=secret_key or settings.STORAGE_SECRET_KEY,
            force_path_style=force_path_style,
        )

    raise ValueError(f"Unknown backend type: {backend_type!r}")


async def _collect_all_files(
    storage: StorageBackend,
    prefix: str = "",
) -> list[tuple[str, int]]:
    """Recursively collect all files under a prefix with their sizes.

    Returns list of (key, size) tuples.
    """
    all_files: list[tuple[str, int]] = []

    async def collect(current_prefix: str) -> None:
        entries = await storage.list(current_prefix)
        for entry in entries:
            if entry.is_dir:
                await collect(entry.path)
            else:
                all_files.append((entry.path, entry.size))

    await collect(prefix)
    return all_files


async def migrate_file(
    src: StorageBackend,
    dst: StorageBackend,
    key: str,
    dry_run: bool = False,
) -> bool:
    """Migrate a single file from source to destination.

    Returns True if successful, False otherwise.
    """
    try:
        if dry_run:
            return True

        # Always use read_bytes/write_bytes for safety with unknown file types
        content = await src.read_bytes(key)
        await dst.write_bytes(key, content)

        # Verify write succeeded
        if not await dst.exists(key):
            print(f"  [ERROR] Verification failed for {key}", file=sys.stderr)
            return False

        return True
    except StorageError as e:
        print(f"  [ERROR] {key}: {e}", file=sys.stderr)
        return False


async def main() -> None:
    """Main migration routine."""
    parser = argparse.ArgumentParser(
        description="Migrate agent workspace files between storage backends.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Migrate from local to S3 (using config settings)
  python -m app.scripts.migrate_storage --from local --to s3

  # Migrate from S3 to local
  python -m app.scripts.migrate_storage --from s3 --to local

  # Dry run (show what would be migrated)
  python -m app.scripts.migrate_storage --from local --to s3 --dry-run

  # Migrate specific agent only
  python -m app.scripts.migrate_storage --from local --to s3 --agent-id <uuid>

  # Migrate enterprise KB only
  python -m app.scripts.migrate_storage --from local --to s3 --enterprise-only

S3 Destination Options:
  If migrating TO s3, you can override config settings with:
    --bucket <name>           S3 bucket name
    --endpoint-url <url>       Custom S3 endpoint
    --region <region>          AWS region
    --access-key <key>         AWS access key
    --secret-key <secret>      AWS secret key
        """,
    )
    parser.add_argument(
        "--from",
        dest="from_backend",
        required=True,
        choices=["local", "s3"],
        help="Source storage backend",
    )
    parser.add_argument(
        "--to",
        dest="to_backend",
        required=True,
        choices=["local", "s3"],
        help="Destination storage backend",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without actually copying files",
    )
    parser.add_argument(
        "--agent-id",
        help="Migrate files for a specific agent ID only",
    )
    parser.add_argument(
        "--enterprise-only",
        action="store_true",
        help="Migrate only enterprise knowledge base files (under 'enterprise/' prefix)",
    )
    parser.add_argument(
        "--bucket",
        help="S3 bucket name (overrides config for --to s3)",
    )
    parser.add_argument(
        "--endpoint-url",
        help="S3 endpoint URL (overrides config for --to s3)",
    )
    parser.add_argument(
        "--region",
        help="S3 region (overrides config for --to s3)",
    )
    parser.add_argument(
        "--access-key",
        help="S3 access key (overrides config for --to s3)",
    )
    parser.add_argument(
        "--secret-key",
        help="S3 secret key (overrides config for --to s3)",
    )

    args = parser.parse_args()

    # Build source prefix
    prefix = ""
    if args.agent_id:
        prefix = args.agent_id
    elif args.enterprise_only:
        prefix = "enterprise"

    print(f"Storage Migration Tool")
    print(f"{'='*60}")
    print(f"Source backend:    {args.from_backend}")
    print(f"Destination backend: {args.to_backend}")
    print(f"Prefix:            {prefix or '(all files)'}")
    print(f"Dry run:           {args.dry_run}")
    print(f"{'='*60}")

    # Create storage backends
    settings = get_settings()
    src = _create_backend(args.from_backend, agent_data_dir=settings.AGENT_DATA_DIR)
    dst = _create_backend(
        args.to_backend,
        bucket=args.bucket,
        endpoint_url=args.endpoint_url,
        region=args.region,
        access_key=args.access_key,
        secret_key=args.secret_key,
        agent_data_dir=settings.AGENT_DATA_DIR,
    )

    print(f"\nSource backend:    {src.backend_name}")
    print(f"Destination backend: {dst.backend_name}\n")

    # Collect all files to migrate
    print("Scanning source storage...")
    try:
        files = await _collect_all_files(src, prefix)
    except StorageError as e:
        print(f"\n[ERROR] Failed to scan source: {e}", file=sys.stderr)
        sys.exit(1)

    if not files:
        print("No files found to migrate.")
        sys.exit(0)

    total_files = len(files)
    total_bytes = sum(size for _, size in files)
    print(f"Found {total_files} files ({total_bytes:,} bytes)\n")

    if args.dry_run:
        print("Files to migrate (dry run):")
        for key, size in files[:10]:  # Show first 10
            print(f"  {key} ({size:,} bytes)")
        if len(files) > 10:
            print(f"  ... and {len(files) - 10} more files")
        sys.exit(0)

    # Migrate files
    print("Starting migration...")
    success_count = 0
    error_count = 0
    errors: list[str] = []

    for idx, (key, size) in enumerate(files, 1):
        if await migrate_file(src, dst, key, dry_run=args.dry_run):
            success_count += 1
        else:
            error_count += 1
            errors.append(key)

        # Progress reporting every 10 files
        if idx % 10 == 0:
            print(f"  Progress: {idx}/{total_files} ({idx*100//total_files}%)")

    # Summary
    print(f"\n{'='*60}")
    print(f"Migration complete!")
    print(f"Total files:        {total_files}")
    print(f"Total bytes:        {total_bytes:,}")
    print(f"Successful:         {success_count}")
    print(f"Errors:             {error_count}")
    print(f"{'='*60}")

    if errors:
        print("\nFailed files:")
        for err in errors[:20]:
            print(f"  - {err}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more errors")

    sys.exit(0 if error_count == 0 else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INFO] Migration cancelled by user", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
