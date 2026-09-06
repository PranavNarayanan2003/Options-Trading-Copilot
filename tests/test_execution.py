import asyncio
import pytest
from app.execution.webull import WebullExecutionAdapter, ExecutionDisabled


def test_execution_is_hard_disabled():
    adapter=WebullExecutionAdapter(enabled=True)
    async def go():
        with pytest.raises(ExecutionDisabled):
            await adapter.place(None)
    asyncio.run(go())
