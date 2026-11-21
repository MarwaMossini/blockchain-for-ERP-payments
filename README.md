# Outage Ledger Lab

A local web demonstration for **Blockchain Under Power and Internet Outages**.
It uses a fictional university payment, five simulated validator nodes, ECDSA
signatures over secp256k1, SHA3-256 block hashes, Merkle roots, and a simplified
3-of-5 quorum rule.

## Run

1. Install Python 3.10 or newer.
2. In this folder, run `python -m pip install -r requirements.txt`.
3. Run `python outage_ledger_demo.py`.
4. Open `http://127.0.0.1:8765` in a browser.

The simulator runs locally and uses imaginary payment units. It does not send
data over the internet.

## Presentation demonstration (about 90 seconds)

1. Make a payment of 30 units while all five nodes are online. Observe **CONFIRMED**.
2. Disconnect the student internet and create another payment. Observe **PENDING**.
3. Reconnect internet, turn off three validators, then click **Reconcile pending**.
   The transaction remains **PENDING** because only two nodes are available.
4. Restore one validator. Click **Reconcile pending**. The transaction becomes
   **CONFIRMED**, and the restored validator catches up by verifying the chain.
5. Click **Try duplicate nonce**. The validly signed but stale transaction is
   **REJECTED** due to its reused nonce.

## Run the tests

```bash
python -m unittest discover -v
```

The tests cover normal confirmation, an offline client, loss and restoration of
validator quorum, duplicate nonces, insufficient balance and hash-chain tampering.

## Code structure

- `outage_ledger_demo.py`: the simulator and local browser interface.
- `test_ledger.py`: repeatable checks of the simulated failure scenarios.
- `requirements.txt`: the Python cryptography dependency.

GitHub stores the source code. The Python web server runs on your own computer;
GitHub Pages cannot execute this Python backend.

## What the simulator does and does not prove

- It demonstrates availability under a simple 3-of-5 majority rule.
- It verifies digital signatures and SHA3-256 hash links.
- Its local queue holds pending transactions while the process stays running.
- Power-off is simulated by toggling validators, not by turning off real machines.
- It is **not an implementation of Raft** or a real financial blockchain.
- Pending offline payments are not final; duplicate/double-spend attempts must
  be checked after reconnecting to the live ledger.
- For production, add persistent storage, secure private-key management,
  authenticated networking, and a thoroughly tested consensus protocol.
