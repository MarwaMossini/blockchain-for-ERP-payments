"""Regression tests for the classroom outage and recovery scenarios."""

import copy
import unittest

from outage_ledger_demo import LedgerSim


class OutageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = LedgerSim()

    def test_normal_payment_commits_and_verifies(self):
        tx = self.ledger.submit(amount=30)
        self.assertEqual(tx["status"], "CONFIRMED")
        self.assertEqual(self.ledger.status()["height"], 1)
        self.assertEqual(self.ledger.balances["University"], 30)
        self.assertTrue(self.ledger.verify_chain(self.ledger.chain))

    def test_student_disconnection_defers_until_reconnection(self):
        self.ledger.toggle_client()
        tx = self.ledger.submit(amount=20)
        self.assertEqual(tx["status"], "PENDING")
        self.assertEqual(self.ledger.status()["height"], 0)
        self.assertFalse(self.ledger.reconcile())

        self.ledger.toggle_client()
        self.assertTrue(self.ledger.reconcile())
        self.assertEqual(tx["status"], "CONFIRMED")

    def test_quorum_loss_stops_new_blocks_and_recovers(self):
        for node_id in ("1", "2", "3"):
            self.ledger.toggle_node(node_id)
        self.assertFalse(self.ledger.status()["quorum"])
        tx = self.ledger.submit(amount=20)
        self.assertEqual(tx["status"], "PENDING")
        self.assertFalse(self.ledger.reconcile())

        self.ledger.toggle_node("1")
        self.assertTrue(self.ledger.status()["quorum"])
        self.assertTrue(self.ledger.reconcile())
        self.assertEqual(tx["status"], "CONFIRMED")
        self.ledger.toggle_node("2")
        self.assertEqual(len(self.ledger.nodes["2"]["ledger"]), 2)

    def test_reused_nonce_is_rejected(self):
        self.assertEqual(self.ledger.submit(amount=30)["status"], "CONFIRMED")
        duplicate = self.ledger.submit(receiver="Bookshop", amount=25, reused_nonce=True)
        self.assertEqual(duplicate["status"], "REJECTED")
        self.assertIn("nonce", duplicate["reason"].lower())

    def test_insufficient_balance_rejected(self):
        self.assertEqual(self.ledger.submit(amount=140)["status"], "CONFIRMED")
        overdraw = self.ledger.submit(amount=20)
        self.assertEqual(overdraw["status"], "REJECTED")
        self.assertEqual(self.ledger.balances["Student"], 10)

    def test_tampered_block_fails_integrity_check(self):
        self.ledger.submit(amount=30)
        altered = copy.deepcopy(self.ledger.chain)
        altered[1]["transactions"][0]["amount"] = 999
        self.assertFalse(self.ledger.verify_chain(altered))


if __name__ == "__main__":
    unittest.main()
