"""Migrate shared agent memory/focus files to per-user directories.

Usage:
    python -m app.scripts.migrate_user_memory --dry-run
    python -m app.scripts.migrate_user_memory
    python -m app.scripts.migrate_user_memory --creator-only
    python -m app.scripts.migrate_user_memory --rollback
"""

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select, distinct

from app.database import async_session
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.services.storage.factory import get_storage


async def _copy_if_exists(storage, src_key: str, dst_key: str) -> bool:
    if await storage.exists(src_key) and not await storage.exists(dst_key):
        content = await storage.read(src_key)
        await storage.write(dst_key, content)
        return True
    return False


async def get_agent_user_pairs() -> list[tuple[uuid.UUID, uuid.UUID]]:
    async with async_session() as db:
        result = await db.execute(
            select(distinct(ChatMessage.agent_id), distinct(ChatMessage.user_id))
            .where(ChatMessage.user_id.isnot(None))
        )
        return list(result.all())


async def get_all_agents() -> list[Agent]:
    async with async_session() as db:
        result = await db.execute(select(Agent))
        return list(result.scalars().all())


async def dry_run():
    storage = get_storage()
    agents = await get_all_agents()

    print(f"=== DRY RUN ===")
    print(f"Found {len(agents)} agents\n")

    total_users = 0
    total_files = 0

    for agent in agents:
        pairs = await get_agent_user_pairs()
        agent_users = [uid for aid, uid in pairs if aid == agent.id]

        if not agent_users:
            print(f"Agent {agent.name} ({agent.id}): no user interactions found")
            continue

        print(f"Agent {agent.name} ({agent.id}): {len(agent_users)} users")
        for uid in agent_users:
            total_users += 1
            mem_key = f"{agent.id}/memory/memory.md"
            focus_key = f"{agent.id}/focus.md"
            files_to_copy = []
            if await storage.exists(mem_key):
                files_to_copy.append("memory/memory.md")
            if await storage.exists(focus_key):
                files_to_copy.append("focus.md")
            total_files += len(files_to_copy)
            print(f"  User {uid}: would copy {files_to_copy}")

    print(f"\nSummary: {len(agents)} agents, {total_users} user directories, {total_files} files to copy")
    print("Shared files will NOT be deleted.")


async def migrate_all():
    storage = get_storage()
    agents = await get_all_agents()

    migrated = 0
    for agent in agents:
        pairs = await get_agent_user_pairs()
        agent_users = list({uid for aid, uid in pairs if aid == agent.id})

        for uid in agent_users:
            copied_mem = await _copy_if_exists(
                storage,
                f"{agent.id}/memory/memory.md",
                f"{agent.id}/users/{uid}/memory/memory.md",
            )
            copied_focus = await _copy_if_exists(
                storage,
                f"{agent.id}/focus.md",
                f"{agent.id}/users/{uid}/focus.md",
            )
            if copied_mem or copied_focus:
                migrated += 1
                print(f"  ✅ Agent {agent.name} / User {uid}: memory={copied_mem} focus={copied_focus}")

    print(f"\nMigration complete: {migrated} user directories created")


async def migrate_creator_only():
    storage = get_storage()
    agents = await get_all_agents()

    migrated = 0
    for agent in agents:
        if not agent.creator_id:
            continue
        uid = agent.creator_id
        copied_mem = await _copy_if_exists(
            storage,
            f"{agent.id}/memory/memory.md",
            f"{agent.id}/users/{uid}/memory/memory.md",
        )
        copied_focus = await _copy_if_exists(
            storage,
            f"{agent.id}/focus.md",
            f"{agent.id}/users/{uid}/focus.md",
        )
        if copied_mem or copied_focus:
            migrated += 1
            print(f"  ✅ Agent {agent.name} / Creator {uid}: memory={copied_mem} focus={copied_focus}")

    print(f"\nCreator-only migration complete: {migrated} agent creators migrated")


async def rollback():
    storage = get_storage()
    agents = await get_all_agents()

    removed = 0
    for agent in agents:
        users_prefix = f"{agent.id}/users/"
        try:
            items = await storage.list(users_prefix)
            if items:
                await storage.delete_prefix(users_prefix)
                removed += 1
                print(f"  ✅ Agent {agent.name}: removed users/ directory")
        except Exception as e:
            print(f"  ❌ Agent {agent.name}: rollback failed: {e}")

    print(f"\nRollback complete: {removed} agents cleaned. System will use shared files.")


def main():
    parser = argparse.ArgumentParser(description="Migrate agent memory/focus to per-user directories")
    parser.add_argument("--dry-run", action="store_true", help="Preview migration plan without making changes")
    parser.add_argument("--creator-only", action="store_true", help="Only migrate to agent creator's user directory")
    parser.add_argument("--rollback", action="store_true", help="Delete all users/ directories, revert to shared mode")
    args = parser.parse_args()

    if args.dry_run:
        asyncio.run(dry_run())
    elif args.rollback:
        asyncio.run(rollback())
    elif args.creator_only:
        asyncio.run(migrate_creator_only())
    else:
        asyncio.run(migrate_all())


if __name__ == "__main__":
    main()
