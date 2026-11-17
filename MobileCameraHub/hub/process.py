import asyncio
import os
import subprocess
from contextlib import suppress


async def spawn(*args, **kwargs):
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return await asyncio.create_subprocess_exec(*map(str, args), **kwargs)


async def stop(process):
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 3)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
