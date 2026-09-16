# order-api

Demo service monitored by Forge. `active_bug.txt` defaults to `1`. There are 10 mutually exclusive bugs; only one is active at a time.

Start it on its own (Forge usually starts it for you):

```bat
python -m uvicorn app:app --host 127.0.0.1 --port 8790
```

To push hotfixes: `git remote add origin <your remote.git>` in this directory, and make sure `git push` already works on the duty machine.
