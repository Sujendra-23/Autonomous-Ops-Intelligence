"""Run with: python -m mock_field_service  (listens on 127.0.0.1:9100).

Environment: MOCK_FS_HOST, MOCK_FS_PORT, MOCK_FS_WEBHOOK_SECRET (must equal AOI's
FIELD_SERVICE_WEBHOOK_SECRET; default is 32 "w" characters).
"""

import os

import uvicorn

from mock_field_service.server import MockState, create_app

if __name__ == "__main__":
    state = MockState(webhook_secret=os.environ.get("MOCK_FS_WEBHOOK_SECRET", "w" * 32))
    uvicorn.run(
        create_app(state),
        host=os.environ.get("MOCK_FS_HOST", "127.0.0.1"),
        port=int(os.environ.get("MOCK_FS_PORT", "9100")),
    )
