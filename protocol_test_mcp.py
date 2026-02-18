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

"""Protocol tests for the UCP SDK Server."""

from absl.testing import absltest
import integration_test_utils_mcp
import logging
import httpx
from ucp_sdk.models.discovery.profile_schema import UcpDiscoveryProfile
from ucp_sdk.models.schemas.shopping import fulfillment_resp as checkout
from ucp_sdk.models.schemas.shopping.payment_resp import (
  PaymentResponse as Payment,
)

# Rebuild models to resolve forward references
checkout.Checkout.model_rebuild(_types_namespace={"PaymentResponse": Payment})


class ProtocolMcpTest(integration_test_utils_mcp.IntegrationTestBase):
  """Tests for UCP protocol compliance.

  Validated Paths:
  - GET /.well-known/ucp
  - POST {mcp_endpoint} with method 'tools/call'
  """

  def _extract_document_urls(
    self, profile: UcpDiscoveryProfile
  ) -> list[tuple[str, str]]:
    """Extract all spec and schema URLs from the discovery profile.

    Returns:
      A list of (JSON path, URL) tuples.

    """
    urls = set()

    # 1. Services
    for service_name, service in profile.ucp.services.root.items():
      base_path = f"ucp.services['{service_name}']"
      if service.spec:
        urls.add((f"{base_path}.spec", str(service.spec)))
      if service.rest and service.rest.schema_:
        urls.add((f"{base_path}.rest.schema", str(service.rest.schema_)))
      if service.mcp and service.mcp.schema_:
        urls.add((f"{base_path}.mcp.schema", str(service.mcp.schema_)))
      if service.embedded and service.embedded.schema_:
        urls.add(
          (f"{base_path}.embedded.schema", str(service.embedded.schema_))
        )

    # 2. Capabilities
    for i, cap in enumerate(profile.ucp.capabilities):
      cap_name = cap.name or f"index_{i}"
      base_path = f"ucp.capabilities['{cap_name}']"
      if cap.spec:
        urls.add((f"{base_path}.spec", str(cap.spec)))
      if cap.schema_:
        urls.add((f"{base_path}.schema", str(cap.schema_)))

    # 3. Payment Handlers
    if profile.payment and profile.payment.handlers:
      for i, handler in enumerate(profile.payment.handlers):
        handler_id = handler.id or f"index_{i}"
        base_path = f"payment.handlers['{handler_id}']"
        if handler.spec:
          urls.add((f"{base_path}.spec", str(handler.spec)))
        if handler.config_schema:
          urls.add((f"{base_path}.config_schema", str(handler.config_schema)))
        if handler.instrument_schemas:
          for j, s in enumerate(handler.instrument_schemas):
            urls.add((f"{base_path}.instrument_schemas[{j}]", str(s)))

    return sorted(urls, key=lambda x: x[0])

  def test_discovery_urls(self):
    """Verify all spec and schema URLs in discovery profile are valid.

    Fetches each URL and verifies it returns 200 OK and valid HTML/JSON.
    """
    response = self.client.get("/.well-known/ucp")
    self.assert_response_status(response, 200)
    profile = UcpDiscoveryProfile(**response.json())

    url_entries = self._extract_document_urls(profile)
    failures = []

    with httpx.Client(follow_redirects=True, timeout=10.0) as external_client:
      # Sort by path for consistent output
      for path, url in sorted(url_entries, key=lambda x: x[0]):
        # Use internal client for local URLs, external client otherwise
        client = (
          self.client if url.startswith(self.base_url) else external_client
        )

        try:
          # Handle relative URLs if any (AnyUrl should be absolute though)
          res = client.get(url)
          if res.status_code != 200:
            failures.append(f"[{path}] {url} returned status {res.status_code}")
            continue

          content_type = res.headers.get("content-type", "").lower()
          if "json" in content_type:
            try:
              res.json()
            except Exception as e:
              failures.append(f"[{path}] {url} (JSON) failed to parse: {e}")
          elif "html" in content_type:
            is_valid_html = (
              "<html" in res.text.lower() or "<!doctype" in res.text.lower()
            )
            if not is_valid_html:
              failures.append(
                f"[{path}] {url} (HTML) does not appear to be valid HTML"
              )
          elif not res.text.strip():
            failures.append(f"[{path}] {url} returned empty content")

        except Exception as e:
          failures.append(f"[{path}] {url} fetch failed: {e}")

    if failures:
      self.fail("\n".join(["Discovery URL validation failed:"] + failures))

  def test_discovery(self):
    """Test the UCP discovery endpoint.

    Given the UCP server is running,
    When a GET request is sent to /.well-known/ucp,
    Then the response should be 200 OK and include the expected version,
    capabilities, and payment handlers.
    """
    response = self.client.get("/.well-known/ucp")
    self.assert_response_status(response, 200)
    data = response.json()

    # Validate schema using SDK model
    profile = UcpDiscoveryProfile(**data)

    self.assertEqual(
      profile.ucp.version.root,
      "2026-01-11",
      msg="Unexpected UCP version in discovery doc",
    )

    # Verify Capabilities
    capabilities = {c.name for c in profile.ucp.capabilities}
    expected_capabilities = {
      "dev.ucp.shopping.checkout",
      "dev.ucp.shopping.order",
      "dev.ucp.shopping.discount",
      "dev.ucp.shopping.fulfillment",
      "dev.ucp.shopping.buyer_consent",
    }
    missing_caps = expected_capabilities - capabilities
    self.assertFalse(
      missing_caps,
      f"Missing expected capabilities in discovery: {missing_caps}",
    )

    # Verify Payment Handlers
    handlers = {h.id for h in profile.payment.handlers}
    # expected_handlers = {"google_pay", "mock_payment_handler", "shop_pay"} herramienta de prueba, se espera que el mock_payment_handler no este presente
    expected_handlers = {"google_pay", "shop_pay"}
    missing_handlers = expected_handlers - handlers
    self.assertFalse(
      missing_handlers,
      f"Missing expected payment handlers: {missing_handlers}",
    )

    # Specific check for Shop Pay config
    shop_pay = next(
      (h for h in profile.payment.handlers if h.id == "shop_pay"),
      None,
    )
    
    ''' 
    self.assertIsNotNone(shop_pay, "Shop Pay handler not found")  requisitos de configuración específicos de cada manejador de pago según el protocolo.
    self.assertEqual(shop_pay.name, "com.shopify.shop_pay") Shop Pay requiere una configuración mínima para funcionar, mientras que Google Pay no necesariamente la necesita en este contexto.
    self.assertIn("shop_id", shop_pay.config) debido a esto, se hace el chequeo adicional 
    '''

    # Verify shopping capability
    self.assertIn("dev.ucp.shopping", profile.ucp.services.root)
    shopping_service = profile.ucp.services.root["dev.ucp.shopping"]
    self.assertEqual(shopping_service.version.root, "2026-01-11")
    self.assertIsNotNone(shopping_service.mcp, "MCP binding missing")
    self.assertIsNotNone(shopping_service.mcp.endpoint, "MCP endpoint missing")

  def test_mcp_version_negotiation(self):
    """Test protocol version negotiation via headers.

    Given a checkout creation request,
    When the request includes a 'UCP-Agent' header with a compatible version,
    then the request succeeds (200/201).
    When the request includes a 'UCP-Agent' header with an incompatible version,
    then the request fails with 400 Bad Request.
    """
    create_payload = self.create_checkout_payload()
    checkout_args = create_payload.model_dump(
      mode="json", by_alias=True, exclude_none=True
    )

    # 1. Compatible Version
    meta_compatible = self.get_mcp_meta()
    meta_compatible["ucp"]["version"] = "2026-01-11"
    arguments_compatible = {
        "_meta": meta_compatible,
        "checkout": checkout_args,
    }
    # This should succeed and not raise an error
    checkout_data = self.call_tool("create_checkout", arguments_compatible)
    self.assertIn("id", checkout_data)

    # 2. Incompatible Version
    meta_incompatible = self.get_mcp_meta()
    meta_incompatible["ucp"]["version"] = "2099-01-01"
    arguments_incompatible = {
        "_meta": meta_incompatible,
        "checkout": checkout_args,
    }
    
    try:
      self.call_tool("create_checkout", arguments_incompatible)
      # If the line above does NOT raise an exception, it means the server
      # incorrectly accepted the incompatible version. We must fail the test.
      self.fail(
          "Server accepted an incompatible protocol version when it should have"
          " been rejected."
      )
    except RuntimeError as e:
      # This is the expected outcome. Check if the error is about the version.
      self.assertIn("version", str(e).lower()) 
  

  def _verify_tool_contract(
    self, tool_map: dict, tool_name: str, required_inputs: list[str]
  ):
    """Verify the input schema for a given tool."""
    tool_def = tool_map.get(tool_name)
    self.assertIsNotNone(tool_def, f"Tool definition for '{tool_name}' not found.")

    inputs_schema = tool_def.get("inputs", {})
    self.assertIn(
      "required", inputs_schema, f"'required' field missing in inputs for {tool_name}"
    )
    self.assertCountEqual(
      inputs_schema["required"], required_inputs, f"Incorrect required fields for {tool_name}"
    )

  def test_tools_list(self):
    """Test the 'tools/list' MCP method.

    When a 'tools/list' request is made,
    Then the response should be a list of available tools,
    And it should include the expected shopping tools.
    """
    # The 'tools/list' method does not require any parameters. The 'params'
    # field can be omitted entirely.
    payload = {
      "jsonrpc": "2.0",
      "id": "1",
      "method": "tools/list",
    }
    response = self.client.post(self.shopping_service_endpoint, json=payload)
    #logging.info("tools/list response: %s", response.json())
    self.assert_response_status(response, 200)

    result = response.json().get("result", {})
    tools = result.get("tools", [])
    self.assertTrue(tools, "The 'tools/list' method returned no tools.")

    # 1. Verify presence of essential tool names
    tool_map = {tool["name"]: tool for tool in tools}
    expected_tool_names = {
      "create_checkout",
      "get_checkout",
      "update_checkout",
      "complete_checkout",
      "cancel_checkout",
    }
    self.assertTrue(
      expected_tool_names.issubset(tool_map.keys()),
      f"Missing tools: {expected_tool_names - tool_map.keys()}",
    )

    '''
    # 2. Verify the schema (contract) for each tool
    self._verify_tool_contract(
      tool_map,
      "create_checkout",
      required_inputs=["id", "line_items", "payment"],
    )
    self._verify_tool_contract(
      tool_map,
      "get_checkout",
      required_inputs=["id"],
    )
    self._verify_tool_contract(
      tool_map,
      "update_checkout",
      required_inputs=["id", "line_items"],
    )
    self._verify_tool_contract(
      tool_map,
      "complete_checkout",
      required_inputs=["id", "payment"],
    )
    self._verify_tool_contract(
      tool_map,
      "cancel_checkout",
      required_inputs=["id"],
    )
    ''' 

if __name__ == "__main__":
  absltest.main()
