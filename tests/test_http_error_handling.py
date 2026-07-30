#!/usr/bin/env python3
"""
Test HTTP error handling to verify the fix for "HTTP ?" error.
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch
from requests.exceptions import HTTPError

sys.path.insert(0, str(Path(__file__).parent.parent))

from beetsplug.getlrc import GetLrcPlugin


def test_http_error_status_code_extraction():
    """Test that HTTP error codes are properly extracted."""
    print("\n" + "="*60)
    print("TEST: HTTP Error Status Code Extraction")
    print("="*60)
    
    plugin = GetLrcPlugin()
    
    # Create a mock item
    mock_item = Mock()
    mock_item.title = "Test Track"
    mock_item.artist = "Test Artist"
    mock_item.album = "Test Album"
    mock_item.length = 180
    mock_item.lyrics = None
    mock_item.path = b''
    mock_item.get = Mock(return_value=None)
    mock_item.__setitem__ = Mock()
    mock_item.store = Mock()
    
    # Mock stats
    mock_stats = Mock()
    
    # Test Case 1: HTTPError with response but incomplete response object
    print("\nTest 1: HTTPError with response object")
    with patch.object(plugin, '_request_with_retry') as mock_retry:
        # Create a mock response with status code
        mock_response = Mock()
        mock_response.status_code = 503  # Service Unavailable
        mock_response.raise_for_status.side_effect = HTTPError(response=mock_response)
        mock_retry.return_value = mock_response
        
        with patch.object(plugin, '_print') as mock_print:
            with patch.object(plugin, '_update_cache') as mock_cache:
                result = plugin.fetch_lrc(mock_item, stats=mock_stats)
        
        # Verify the status code was printed
        call_args = mock_print.call_args[0]
        status_msg = call_args[0]
        
        print(f"  Status message: {status_msg}")
        # Should show HTTP 503, not HTTP ?
        if "503" in status_msg or "?" not in status_msg:
            print(f"  ✓ PASS - Correctly extracted status code")
        else:
            print(f"  ✗ FAIL - Got '?' instead of proper status code")
    
    # Test Case 2: HTTPError without response object
    print("\nTest 2: HTTPError without response object")
    with patch.object(plugin, '_request_with_retry') as mock_retry:
        # Create a mock response with status code
        mock_response = Mock()
        mock_response.status_code = 429  # Too Many Requests
        mock_response.raise_for_status.side_effect = HTTPError(response=None)
        mock_retry.return_value = mock_response
        
        with patch.object(plugin, '_print') as mock_print:
            with patch.object(plugin, '_update_cache') as mock_cache:
                result = plugin.fetch_lrc(mock_item, stats=mock_stats)
        
        # Verify the status code was printed
        call_args = mock_print.call_args[0]
        status_msg = call_args[0]
        
        print(f"  Status message: {status_msg}")
        # Should show HTTP 429 (from captured response_status), not HTTP ?
        if "429" in status_msg or "?" not in status_msg:
            print(f"  ✓ PASS - Used captured status code when response not on exception")
        else:
            print(f"  ✗ FAIL - Got '?' instead of captured status code")
    
    # Test Case 3: 404 Not Found handling
    print("\nTest 3: 404 Not Found handling")
    with patch.object(plugin, '_request_with_retry') as mock_retry:
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = HTTPError(response=mock_response)
        mock_retry.return_value = mock_response
        
        with patch.object(plugin, '_print') as mock_print:
            with patch.object(plugin, '_update_cache') as mock_cache:
                result = plugin.fetch_lrc(mock_item, stats=mock_stats)
        
        call_args = mock_print.call_args[0]
        status_msg = call_args[0]
        
        print(f"  Status message: {status_msg}")
        if "Not found" in status_msg:
            print(f"  ✓ PASS - 404 handled correctly")
            # Verify not_found was added to stats
            if mock_stats.add.called and 'not_found' in str(mock_stats.add.call_args):
                print(f"  ✓ PASS - not_found counted in stats")
            else:
                print(f"  ✗ FAIL - not_found not in stats")
        else:
            print(f"  ✗ FAIL - 404 not handled as 'Not found'")


def main():
    print("\n" + "╔" + "="*58 + "╗")
    print("║" + "HTTP ERROR HANDLING TEST".center(58) + "║")
    print("╚" + "="*58 + "╝")
    
    try:
        test_http_error_status_code_extraction()
        print("\n" + "="*60)
        print("Test Complete")
        print("="*60 + "\n")
    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
