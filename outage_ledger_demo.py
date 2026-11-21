"""Outage Ledger Lab: a teaching simulator for blockchain availability.

Run: python outage_ledger_demo.py
Open: http://127.0.0.1:8765

This is a quorum-inspired simulation, NOT a Raft implementation, a banking
system, or a way to make offline payments final. All payments use demo units.
"""

import hashlib
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha3(value):
    return hashlib.sha3_256(value).hexdigest()


def merkle_root(transactions):
    level = [sha3(canonical(t)) for t in transactions]
    if not level:
        return sha3(b"")
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha3(bytes.fromhex(level[i]) + bytes.fromhex(level[i + 1]))
                 for i in range(0, len(level), 2)]
    return level[0]


class LedgerSim:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with getattr(self, "lock", threading.RLock()):
            self.keys = {name: ec.generate_private_key(ec.SECP256K1())
                         for name in ("Student", "University", "Bookshop")}
            self.balances = {"Student": 150, "University": 0, "Bookshop": 0}
            self.next_nonce = {name: 0 for name in self.keys}
            self.client_online = True
            self.nodes = {str(i): {"online": True, "ledger": []} for i in range(1, 6)}
            self.transactions = []
            self.events = []
            genesis = self._block([], "0" * 64, 0)
            self.chain = [genesis]
            for node in self.nodes.values():
                node["ledger"] = [genesis.copy()]
            self.log("Five validator nodes ready. The genesis block is replicated.")

    def log(self, message):
        self.events.insert(0, {"at": time.strftime("%H:%M:%S"), "message": message})
        self.events = self.events[:20]

    def _block(self, transactions, previous, index):
        header = {"index": index, "previous_hash": previous,
                  "merkle_root": merkle_root(transactions), "transactions": transactions}
        return {**header, "hash": sha3(canonical(header))}

    def _quorum(self):
        return sum(n["online"] for n in self.nodes.values()) >= 3

    def _sign(self, sender, receiver, amount, nonce):
        payload = {"id": str(uuid.uuid4())[:8], "sender": sender,
                   "receiver": receiver, "amount": amount, "nonce": nonce}
        signature = self.keys[sender].sign(canonical(payload), ec.ECDSA(hashes.SHA3_256()))
        return {**payload, "signature": signature.hex(), "status": "PENDING", "reason": ""}

    def _valid_signature(self, transaction):
        payload = {k: transaction[k] for k in ("id", "sender", "receiver", "amount", "nonce")}
        try:
            self.keys[transaction["sender"]].public_key().verify(
                bytes.fromhex(transaction["signature"]),
                canonical(payload), ec.ECDSA(hashes.SHA3_256()))
            return True
        except (InvalidSignature, ValueError, KeyError):
            return False

    def submit(self, receiver="University", amount=30, reused_nonce=False):
        with self.lock:
            if receiver not in ("University", "Bookshop"):
                raise ValueError("Receiver must be University or Bookshop")
            if not isinstance(amount, int) or amount <= 0 or amount > 150:
                raise ValueError("Choose a positive integer amount up to 150 demo units")
            nonce = self.next_nonce["Student"]
            if reused_nonce:
                nonce = max(0, nonce - 1)
            transaction = self._sign("Student", receiver, amount, nonce)
            self.transactions.append(transaction)
            if self.client_online and self._quorum():
                self._process_one(transaction)
            else:
                reason = "client disconnected" if not self.client_online else "quorum unavailable"
                self.log(f"Transaction {transaction['id']} signed locally and queued ({reason}).")
            return transaction

    def _process_one(self, tx):
        if not self._valid_signature(tx):
            tx.update(status="REJECTED", reason="Invalid ECDSA signature")
        elif tx["nonce"] != self.next_nonce[tx["sender"]]:
            tx.update(status="REJECTED", reason="Duplicate or stale transaction nonce")
        elif self.balances[tx["sender"]] < tx["amount"]:
            tx.update(status="REJECTED", reason="Insufficient balance at settlement")
        else:
            self.balances[tx["sender"]] -= tx["amount"]
            self.balances[tx["receiver"]] += tx["amount"]
            self.next_nonce[tx["sender"]] += 1
            entry = {k: tx[k] for k in ("id", "sender", "receiver", "amount", "nonce", "signature")}
            block = self._block([entry], self.chain[-1]["hash"], len(self.chain))
            self.chain.append(block)
            for node in self.nodes.values():
                if node["online"]:
                    node["ledger"].append(block.copy())
            tx.update(status="CONFIRMED", reason=f"Block #{block['index']}")
        self.log(f"Transaction {tx['id']}: {tx['status']} ({tx['reason']}).")

    def reconcile(self):
        with self.lock:
            if not self.client_online or not self._quorum():
                self.log("Reconciliation deferred until the client and a node quorum are connected.")
                return False
            for tx in self.transactions:
                if tx["status"] == "PENDING":
                    self._process_one(tx)
            self.log("Pending queue checked against the current ledger state.")
            return True

    def toggle_client(self):
        with self.lock:
            self.client_online = not self.client_online
            self.log("Student wallet online." if self.client_online else "Student wallet internet disconnected.")

    def toggle_node(self, node_id):
        with self.lock:
            if node_id not in self.nodes:
                raise ValueError("Unknown validator")
            node = self.nodes[node_id]
            node["online"] = not node["online"]
            if node["online"]:
                if not self.verify_chain(self.chain):
                    raise RuntimeError("Canonical chain failed hash verification")
                missing = len(self.chain) - len(node["ledger"])
                node["ledger"] = [block.copy() for block in self.chain]
                self.log(f"Validator {node_id} restored, verified SHA3-256 links, synced {missing} block(s).")
            else:
                self.log(f"Validator {node_id} powered off or disconnected.")

    @staticmethod
    def verify_chain(chain):
        for i, block in enumerate(chain):
            header = {k: block[k] for k in ("index", "previous_hash", "merkle_root", "transactions")}
            if block["index"] != i or sha3(canonical(header)) != block["hash"]:
                return False
            if merkle_root(block["transactions"]) != block["merkle_root"]:
                return False
            if i and block["previous_hash"] != chain[i - 1]["hash"]:
                return False
        return True

    def status(self):
        with self.lock:
            return {"client_online": self.client_online,
                    "quorum": self._quorum(), "balances": self.balances,
                    "nodes": [{"id": node_id, "online": n["online"],
                               "blocks": len(n["ledger"])} for node_id, n in self.nodes.items()],
                    "transactions": [{k: tx[k] for k in ("id", "receiver", "amount", "nonce", "status", "reason")}
                                     for tx in reversed(self.transactions)],
                    "height": len(self.chain) - 1,
                    "latest_hash": self.chain[-1]["hash"],
                    "latest_merkle": self.chain[-1]["merkle_root"],
                    "integrity": self.verify_chain(self.chain),
                    "events": self.events}


sim = LedgerSim()

HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Outage Ledger Lab</title>
<style>
:root{--dark:#202739;--wine:#a72a45;--line:#dce0e6;--muted:#687083;--bg:#f7f8fb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:16px/1.45 system-ui,Arial;color:var(--dark)}
header{background:#fff;border-bottom:4px solid var(--wine);padding:23px max(24px,calc((100vw - 1120px)/2))}
h1{margin:0;font-size:30px}header p{margin:5px 0 0;color:var(--muted)}main{max-width:1120px;margin:auto;padding:24px}
.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:18px}.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:18px}
h2{margin:0 0 14px;font-size:20px}button{border:0;border-radius:7px;background:var(--wine);color:white;font-weight:700;padding:10px 14px;cursor:pointer}
button.secondary{background:#3c536c}button.outline{background:#f5e8eb;color:#8d1632}button:disabled{opacity:.5;cursor:default}
.nodegrid{display:grid;grid-template-columns:repeat(5,1fr);gap:9px}.node{border:1px solid var(--line);border-radius:8px;padding:15px 6px;background:#effaf3;text-align:center}
.node.off{background:#fff0f0;color:#8b3030}.node small{display:block;color:var(--muted)}.node button{font-size:12px;margin-top:8px;padding:7px 9px}
.status{padding:11px 12px;border-radius:8px;background:#e5f7eb;color:#20613c;font-weight:700;margin:16px 0}.status.bad{background:#fff1d9;color:#825400}
label{display:inline-flex;flex-direction:column;gap:5px;font-size:13px;color:var(--muted);margin-right:10px}
input,select{font:inherit;border:1px solid #bdc6d4;border-radius:6px;padding:8px;background:white;color:#202739}
.flex{display:flex;gap:9px;align-items:end;flex-wrap:wrap}.muted{font-size:13px;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:8px}th{color:#647083}
.pending{color:#9a6600}.confirmed{color:#187844}.rejected{color:#a42d3a}.mono{font:12px ui-monospace,Consolas,monospace;word-break:break-all}
.event{padding:7px 0;border-bottom:1px solid #edf0f4;font-size:13px}footer{font-size:12px;color:var(--muted);padding:8px 0 18px}
@media(max-width:800px){.grid{grid-template-columns:1fr}.nodegrid{grid-template-columns:repeat(3,1fr)}}
</style></head><body>
<header><h1>Outage Ledger Lab</h1><p>A university fee-payment example | SHA3-256, ECDSA, Merkle roots, and simulated quorum</p></header>
<main><div class="grid"><section>
<div class="panel"><h2>1. Blockchain validators</h2><p class="muted">Click nodes to simulate power loss. At least 3 of 5 are needed to commit a block.</p><div class="nodegrid" id="nodes"></div><div id="quorum" class="status"></div>
<button class="secondary" onclick="act('toggle_client')" id="client"></button></div>
<div class="panel"><h2>2. Sign a tuition payment</h2><p class="muted">The wallet signs locally. Without connectivity or quorum, the transaction remains pending.</p><div class="flex"><label>Recipient<select id="receiver"><option>University</option><option>Bookshop</option></select></label><label>Amount (demo units)<input type="number" id="amount" value="30" min="1" max="150"></label><button onclick="pay()">Create signed transaction</button></div>
<div class="flex" style="margin-top:13px"><button class="secondary" onclick="act('reconcile')">Reconcile pending</button><button class="outline" onclick="act('reused_nonce')">Try duplicate nonce</button></div>
<p class="muted">Duplicate nonce creates a second valid signature using an already-used nonce. Signature validity does not guarantee payment acceptance.</p></div>
<div class="panel"><h2>3. Transaction outcomes</h2><table><thead><tr><th>ID</th><th>Payment</th><th>Nonce</th><th>Status</th></tr></thead><tbody id="txs"></tbody></table></div>
</section><section>
<div class="panel"><h2>Ledger state</h2><div id="balances"></div><p><b>Committed height:</b> <span id="height"></span></p><p><b>Chain valid:</b> <span id="integrity"></span></p><p class="muted">Latest block hash (SHA3-256)</p><p class="mono" id="hash"></p><p class="muted">Latest Merkle root</p><p class="mono" id="merkle"></p><button class="outline" onclick="act('reset')">Reset demonstration</button></div>
<div class="panel"><h2>Event log</h2><div id="events"></div></div>
</section></div><footer>Teaching simulator only. Quorum replication is inspired by Raft but does not implement leader election, log replication safety, or a production consensus protocol. No real money is transferred.</footer></main>
<script>
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
async function api(action,payload={}){const r=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const x=await r.json();if(!r.ok)alert(x.error||'Action failed');await refresh()}
function act(action){return api(action)}function pay(){return api('pay',{receiver:document.getElementById('receiver').value,amount:Number(document.getElementById('amount').value)})}
async function refresh(){const s=await(await fetch('/api/status')).json();document.getElementById('nodes').innerHTML=s.nodes.map(n=>`<div class="node ${n.online?'':'off'}"><b>N${n.id}</b><small>${n.online?'Online':'Offline'}</small><small>${n.blocks} blocks</small><button class="${n.online?'outline':'secondary'}" onclick="api('node',{id:'${n.id}'})">${n.online?'Turn off':'Restore'}</button></div>`).join('');
document.getElementById('quorum').textContent=s.quorum?'Quorum available: transactions can commit':'No quorum: new transactions cannot commit';document.getElementById('quorum').className='status '+(s.quorum?'':'bad');
document.getElementById('client').textContent=s.client_online?'Disconnect student internet':'Reconnect student internet';
document.getElementById('balances').innerHTML=Object.entries(s.balances).map(([k,v])=>`<p><b>${k}:</b> ${v} units</p>`).join('');
document.getElementById('height').textContent=s.height;document.getElementById('integrity').textContent=s.integrity?'Yes':'NO';document.getElementById('hash').textContent=s.latest_hash;document.getElementById('merkle').textContent=s.latest_merkle;
document.getElementById('txs').innerHTML=s.transactions.map(t=>`<tr title="${esc(t.reason)}"><td>${esc(t.id)}</td><td>${esc(t.amount)} to ${esc(t.receiver)}</td><td>${t.nonce}</td><td class="${t.status.toLowerCase()}">${t.status}</td></tr>`).join('')||'<tr><td colspan="4">No transactions yet</td></tr>';
document.getElementById('events').innerHTML=s.events.map(e=>`<div class="event"><span class="muted">${e.at}</span> ${esc(e.message)}</div>`).join('');}
refresh();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, status=200, html=False):
        data = (obj if isinstance(obj, str) else json.dumps(obj)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(HTML, html=True)
        elif self.path == "/api/status":
            self._send(sim.status())
        else:
            self._send({"error": "Not found"}, 404)

    def do_POST(self):
        if not self.path.startswith("/api/"):
            return self._send({"error": "Not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 20000:
                raise ValueError("Request too large")
            values = json.loads(self.rfile.read(length) or b"{}")
            action = self.path[5:]
            if action == "node":
                sim.toggle_node(str(values.get("id")))
            elif action == "toggle_client":
                sim.toggle_client()
            elif action == "pay":
                sim.submit(values.get("receiver", "University"), values.get("amount", 30))
            elif action == "reused_nonce":
                sim.submit("Bookshop", 25, reused_nonce=True)
            elif action == "reconcile":
                sim.reconcile()
            elif action == "reset":
                sim.reset()
            else:
                return self._send({"error": "Unknown action"}, 404)
            self._send(sim.status())
        except (ValueError, TypeError, KeyError) as exc:
            self._send({"error": str(exc)}, 400)


if __name__ == "__main__":
    print("Outage Ledger Lab: http://127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
