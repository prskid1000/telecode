import asyncio, json, sys
from pathlib import Path
# Ensure docgraph package from the docgraph repo is importable
sys.path.insert(0, r"c:\Users\prith\\.docgraph")
from docgraph.process import add_doc_for

path = r"C:\Users\prith\\.docgraph"
url = "https://github.com/prskid1000/claude-claw-skill/blob/main/README.md"

async def progress_cb(job):
    try:
        print("PROGRESS_CB:", json.dumps(job))
    except Exception:
        print("PROGRESS_CB: <bad job>")

async def main():
    print('Calling add_doc_for...')
    ok, payload = await add_doc_for(path, url, progress_cb=progress_cb)
    print('RESULT:', ok)
    print('PAYLOAD:', payload)

if __name__ == '__main__':
    asyncio.run(main())
