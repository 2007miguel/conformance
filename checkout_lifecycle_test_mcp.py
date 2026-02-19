#   Copyright 2026 UCP Authors
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""Checkout Lifecycle tests for the UCP SDK Server."""

from absl.testing import absltest
import logging
import integration_test_utils_mcp
from ucp_sdk.models.schemas.shopping import checkout_update_req
from ucp_sdk.models.schemas.shopping import fulfillment_resp as checkout
from ucp_sdk.models.schemas.shopping import payment_update_req
from ucp_sdk.models.schemas.shopping.payment_resp import (
  PaymentResponse as Payment,
)
from ucp_sdk.models.schemas.shopping.types import item_update_req
from ucp_sdk.models.schemas.shopping.types import line_item_update_req

# Rebuild models to resolve forward references
checkout.Checkout.model_rebuild(_types_namespace={"PaymentResponse": Payment})


class CheckoutLifecycleTest(integration_test_utils_mcp.IntegrationTestBase):
  """Tests for the lifecycle of a checkout session.

  Validated MCP Tools:
  - create_checkout
  - get_checkout
  - update_checkout
  - complete_checkout
  - cancel_checkout
  """

  def test_create_checkout(self):
    """Test successful checkout creation.

    Given a valid checkout creation payload,
    When a POST request is sent to /checkout-sessions,
    Then the response should be successful and include a checkout ID.
    """
    response_json = self.create_checkout_session()
    created_checkout = checkout.Checkout(**response_json)
    self.assertTrue(created_checkout.id, "Created checkout missing ID")

  def test_get_checkout(self):
    """Test successful checkout retrieval.

    Given an existing checkout session,
    When a GET request is sent to /checkout-sessions/{id},
    Then the 'get_checkout' tool should return the correct checkout data.
    """
    response_json = self.create_checkout_session()
    checkout_id = checkout.Checkout(**response_json).id

    arguments = {"_meta": self.get_mcp_meta(), "id": checkout_id}
    response_json = self.call_tool("get_checkout", arguments)

    retrieved_checkout = checkout.Checkout(**response_json)
    self.assertEqual(
      retrieved_checkout.id,
      checkout_id,
      msg="Get checkout returned wrong ID",
    )

  def test_update_checkout(self):
    """Test successful checkout update.

    Given an existing checkout session,
    When a PUT request is sent to /checkout-sessions/{id} with updated line
    items, Then the 'update_checkout' tool should reflect the updates.
    """
    response_json = self.create_checkout_session()
    checkout_obj = checkout.Checkout(**response_json)
    checkout_id = checkout_obj.id

    # Construct Update Request
    item_update = item_update_req.ItemUpdateRequest(
      id=checkout_obj.line_items[0].item.id,
      title=checkout_obj.line_items[0].item.title,
    )
    line_item_update = line_item_update_req.LineItemUpdateRequest(
      id=checkout_obj.line_items[0].id,
      item=item_update,
      quantity=2,
    )

    payment_update = payment_update_req.PaymentUpdateRequest(
      selected_instrument_id=checkout_obj.payment.selected_instrument_id,
      instruments=checkout_obj.payment.instruments,
      handlers=[
        h.model_dump(mode="json", exclude_none=True)
        for h in checkout_obj.payment.handlers
      ],
    )

    update_payload = checkout_update_req.CheckoutUpdateRequest(
      id=checkout_id,
      currency=checkout_obj.currency,
      line_items=[line_item_update],
      payment=payment_update,
    )

    arguments = {
        "_meta": self.get_mcp_meta(),
        "id": checkout_id,
        "checkout": update_payload.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ),
    }
    logging.info("update_checkout arguments: %s", arguments)
    # The update_checkout_session helper could also be used here.
    self.call_tool("update_checkout", arguments)

  def test_cancel_checkout(self):
    """Test successful checkout cancellation.

    Given an existing checkout session in progress,
    When a POST request is sent to /checkout-sessions/{id}/cancel,
    Then the response should be 200 OK and the status should update to
    'canceled' via the 'cancel_checkout' tool.
    """
    response_json = self.create_checkout_session()
    checkout_id = checkout.Checkout(**response_json).id

    arguments = {"_meta": self.get_mcp_meta(), "id": checkout_id}
    response_json = self.call_tool("cancel_checkout", arguments)

    canceled_checkout = checkout.Checkout(**response_json)
    self.assertEqual(
      canceled_checkout.status,
      "canceled",
      msg=f"Checkout status not 'canceled', got '{canceled_checkout.status}'",
    )

  def test_complete_checkout(self):
    """Test successful checkout completion.

    Given an existing checkout session with valid payment details,
    When a POST request is sent to /checkout-sessions/{id}/complete,
    Then the response should be 200 OK, the status should be 'completed', and an
    order ID should be generated via the 'complete_checkout' tool.
    """
    response_json = self.create_checkout_session()
    checkout_obj = checkout.Checkout(**response_json)
    checkout_id = checkout_obj.id

    # The helper handles getting a valid payment payload.
    response_json = self.complete_checkout_session(checkout_id)

    completed_checkout = checkout.Checkout(**response_json)
    self.assertEqual(
      completed_checkout.status,
      "completed",
      msg=(
        f"Checkout status not 'completed', got '{completed_checkout.status}'"
      ),
    )
    self.assertIsNotNone(
      completed_checkout.order, "order object missing in completion response"
    )
    self.assertTrue(
      completed_checkout.order.id,
      "order.id missing",
    )
    self.assertTrue(
      completed_checkout.order.permalink_url,
      "order.permalink_url missing",
    )

  def _cancel_checkout(self, checkout_id):
    """Cancel a checkout."""
    arguments = {"_meta": self.get_mcp_meta(), "id": checkout_id}
    response_json = self.call_tool("cancel_checkout", arguments)
    return response_json

  def test_cancel_is_idempotent(self):
    """Test that cancellation is idempotent.

    Given a checkout session that has already been canceled,
    When another cancel request is sent,
    Then the server should reject it with a 409 Conflict (or handle idempotency
    if key matches, but here we test state conflict logic).
    """
    response_json = self.create_checkout_session()
    checkout_id = checkout.Checkout(**response_json).id

    # 1. Cancel
    self._cancel_checkout(checkout_id)

    # 2. Cancel again - should fail. The call_tool helper will raise a
    # RuntimeError if the server returns a JSON-RPC error, which is what we
    # expect.
    with self.assertRaises(
        RuntimeError,
        msg="Should not be able to cancel an already canceled checkout.",
    ):
      arguments = {"_meta": self.get_mcp_meta(), "id": checkout_id}
      self.call_tool("cancel_checkout", arguments)

  def test_cannot_update_canceled_checkout(self):
    """Test that a canceled checkout cannot be updated.

    Given a canceled checkout session,
    When an update request is sent,
    Then the server should reject it with a non-200 status (likely 409).
    """
    response_json = self.create_checkout_session()
    checkout_obj = checkout.Checkout(**response_json)
    checkout_id = checkout_obj.id

    self._cancel_checkout(checkout_id)

    # Try Update
    item_update = item_update_req.ItemUpdateRequest(
      id=checkout_obj.line_items[0].item.id,
      title=checkout_obj.line_items[0].item.title,
    )
    line_item_update = line_item_update_req.LineItemUpdateRequest(
      id=checkout_obj.line_items[0].id,
      item=item_update,
      quantity=2,
    )
    payment_update = payment_update_req.PaymentUpdateRequest(
      selected_instrument_id=checkout_obj.payment.selected_instrument_id,
      instruments=checkout_obj.payment.instruments,
      handlers=[
        h.model_dump(mode="json", exclude_none=True)
        for h in checkout_obj.payment.handlers
      ],
    )
    update_payload = checkout_update_req.CheckoutUpdateRequest(
      id=checkout_id,
      currency=checkout_obj.currency,
      line_items=[line_item_update],
      payment=payment_update,
    )

    with self.assertRaises(
        RuntimeError, msg="Should not be able to update a canceled checkout."
    ):
      arguments = {
          "_meta": self.get_mcp_meta(),
          "checkout": update_payload.model_dump(
              mode="json", by_alias=True, exclude_none=True
          ),
      }
      self.call_tool("update_checkout", arguments)

  def test_cannot_complete_canceled_checkout(self):
    """Test that a canceled checkout cannot be completed.

    Given a canceled checkout session,
    When a complete request is sent,
    Then the server should reject it with a non-200 status.
    """
    response_json = self.create_checkout_session()
    checkout_id = checkout.Checkout(**response_json).id

    self._cancel_checkout(checkout_id)

    # Try Complete
    with self.assertRaises(
        RuntimeError, msg="Should not be able to complete a canceled checkout."
    ):
      self.complete_checkout_session(checkout_id)

  def _complete_checkout(self, checkout_id):
    """Complete a checkout."""
    response_json = self.complete_checkout_session(checkout_id)
    return response_json

  def test_complete_is_idempotent(self):
    """Tests that completing an already completed checkout behaves correctly.

    # Note: checkout_service.py raises CheckoutNotModifiableError (409) if
    # status is COMPLETED. Idempotency is handled by the idempotency key
    # check BEFORE the status check. If we use a different key (which
    # default get_headers does), it should fail.
    """
    response_json = self.create_checkout_session()
    checkout_id = checkout.Checkout(**response_json).id

    self._complete_checkout(checkout_id)

    # Try Complete again (new idempotency key)
    with self.assertRaises(
        RuntimeError,
        msg="Should not be able to complete an already completed checkout.",
    ):
      self.complete_checkout_session(checkout_id)

  def test_cannot_update_completed_checkout(self):
    """Test that a completed checkout cannot be updated.

    Given a completed checkout session,
    When an update request is sent,
    Then the server should reject it with a non-200 status.
    """
    response_json = self.create_checkout_session()
    checkout_obj = checkout.Checkout(**response_json)
    checkout_id = checkout_obj.id

    self._complete_checkout(checkout_id)

    # Try Update
    item_update = item_update_req.ItemUpdateRequest(
      id=checkout_obj.line_items[0].item.id,
      title=checkout_obj.line_items[0].item.title,
    )
    line_item_update = line_item_update_req.LineItemUpdateRequest(
      id=checkout_obj.line_items[0].id,
      item=item_update,
      quantity=2,
    )
    payment_update = payment_update_req.PaymentUpdateRequest(
      selected_instrument_id=checkout_obj.payment.selected_instrument_id,
      instruments=checkout_obj.payment.instruments,
      handlers=[
        h.model_dump(mode="json", exclude_none=True)
        for h in checkout_obj.payment.handlers
      ],
    )
    update_payload = checkout_update_req.CheckoutUpdateRequest(
      id=checkout_id,
      currency=checkout_obj.currency,
      line_items=[line_item_update],
      payment=payment_update,
    )

    with self.assertRaises(
        RuntimeError, msg="Should not be able to update a completed checkout."
    ):
      arguments = {
          "_meta": self.get_mcp_meta(),
          "checkout": update_payload.model_dump(
              mode="json", by_alias=True, exclude_none=True
          ),
      }
      self.call_tool("update_checkout", arguments)

  def test_cannot_cancel_completed_checkout(self):
    """Test that a completed checkout cannot be canceled.

    Given a completed checkout session,
    When a cancel request is sent,
    Then the server should reject it with a non-200 status.
    """
    response_json = self.create_checkout_session()
    checkout_id = checkout.Checkout(**response_json).id

    self._complete_checkout(checkout_id)

    # Try Cancel
    with self.assertRaises(
        RuntimeError, msg="Should not be able to cancel a completed checkout."
    ):
      self._cancel_checkout(checkout_id)


if __name__ == "__main__":
  absltest.main()
