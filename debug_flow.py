"""Debug script to trace the E2E test flow."""

import sys

sys.path.insert(0, "backend/src")

# Minimal stubs
import types

for mod_name in ["anthropic", "anthropic.types", "daytona_sdk", "daytona_sdk.daytona"]:
    stub = types.ModuleType(mod_name)
    stub.Daytona = type("Daytona", (), {})
    stub.DaytonaConfig = type("DaytonaConfig", (), {})
    stub.CreateSandboxParams = type("CreateSandboxParams", (), {})
    stub.APIError = type("APIError", (Exception,), {})
    sys.modules.setdefault(mod_name, stub)

from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

# Now import project modules
from db.base import Base
import db.models
import agents.db.model

# Create test DB
import tempfile

tmp = tempfile.mkdtemp()
db_path = f"{tmp}/test.db"
engine = create_engine(f"sqlite:///{db_path}", echo=False)
Base.metadata.create_all(engine)
sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Import after DB setup
from tools import create_default_tool_registry
from tools.factory import has_factory, create_toolkit, ToolkitContext

# Check factory
print(f"Factory 'sandbox_operations' exists: {has_factory('sandbox_operations')}")

# Simulate spawn_agent toolkit registration
ctx = ToolkitContext(agent_name="test", cwd="/tmp", metadata={})
tk = create_toolkit("sandbox_operations", ctx)
registry = create_default_tool_registry()
registry.register_toolkit(tk)
registry.restrict_to_toolkits(["sandbox_operations"])
schema = registry.to_api_schema()
print(f"Tool count in schema: {len(schema)}")
print(f"Tool names: {[s['name'] for s in schema]}")
