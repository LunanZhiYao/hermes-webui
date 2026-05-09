"""Test that HTML preview works in iframe without X-Frame-Options: DENY."""
import pytest
import json
import tempfile
from pathlib import Path


def test_html_preview_no_xframe_deny():
    """When serving HTML with ?inline=1, X-Frame-Options should not be DENY."""
    # This test verifies the fix for the issue where HTML preview in workspace
    # panel failed with "Refused to display in a frame because it set 
    # 'X-Frame-Options' to 'deny'"
    
    from api.helpers import _security_headers
    
    # Mock handler to capture headers
    class MockHandler:
        def __init__(self):
            self.headers = {}
        
        def send_header(self, name, value):
            self.headers[name] = value
    
    # Test 1: Normal response should have X-Frame-Options: DENY
    handler1 = MockHandler()
    _security_headers(handler1, allow_iframe=False)
    assert handler1.headers.get('X-Frame-Options') == 'DENY', \
        "Normal responses must have X-Frame-Options: DENY"
    
    # Test 2: HTML preview response should NOT have X-Frame-Options
    handler2 = MockHandler()
    _security_headers(handler2, allow_iframe=True)
    assert 'X-Frame-Options' not in handler2.headers, \
        "HTML preview responses should not have X-Frame-Options header"
    
    # Both should still have other security headers
    assert handler1.headers.get('X-Content-Type-Options') == 'nosniff'
    assert handler2.headers.get('X-Content-Type-Options') == 'nosniff'
    assert handler1.headers.get('Referrer-Policy') == 'same-origin'
    assert handler2.headers.get('Referrer-Policy') == 'same-origin'


def test_serve_file_bytes_allows_iframe_for_html():
    """_serve_file_bytes should skip X-Frame-Options when CSP sandbox is set."""
    from api.routes import _serve_file_bytes
    import io
    
    # Create a temporary HTML file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
        f.write('<html><body><h1>Test</h1></body></html>')
        temp_path = Path(f.name)
    
    try:
        # Mock handler
        class MockHandler:
            def __init__(self):
                self.response_code = None
                self.headers = {}
                self.wfile = io.BytesIO()
            
            def send_response(self, code):
                self.response_code = code
            
            def send_header(self, name, value):
                self.headers.setdefault(name, []).append(value)
            
            def end_headers(self):
                pass
        
        # Test with CSP sandbox (HTML inline preview)
        handler = MockHandler()
        _serve_file_bytes(
            handler, 
            temp_path, 
            'text/html', 
            'inline', 
            'no-store',
            csp='sandbox allow-scripts'
        )
        
        # Should NOT have X-Frame-Options when CSP sandbox is present
        xframe_values = handler.headers.get('X-Frame-Options', [])
        assert 'DENY' not in xframe_values, \
            "HTML preview with CSP sandbox should not have X-Frame-Options: DENY"
        
        # Should have CSP sandbox
        csp_values = handler.headers.get('Content-Security-Policy', [])
        assert any('sandbox' in v for v in csp_values), \
            "Should have Content-Security-Policy with sandbox"
        
    finally:
        temp_path.unlink()


def test_workspace_js_passes_inline_param():
    """Verify workspace.js passes &inline=1 for HTML preview."""
    with open("static/workspace.js", "r", encoding="utf-8") as f:
        content = f.read()
    
    assert "inline=1" in content, \
        "workspace.js must pass ?inline=1 to api/file/raw for HTML preview"
    
    # Check the specific line for HTML preview
    assert "&inline=1" in content or "?inline=1" in content, \
        "Inline parameter must be properly formatted in URL"


if __name__ == "__main__":
    test_html_preview_no_xframe_deny()
    test_serve_file_bytes_allows_iframe_for_html()
    test_workspace_js_passes_inline_param()
    print("All tests passed!")
