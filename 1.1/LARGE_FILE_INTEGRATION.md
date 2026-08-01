# Large File Support (3GB Videos)

## Architecture

### Problem
- WebSocket has 1MB limit (increased to 10MB, but still not enough for 3GB videos)
- Agent needs to **receive** videos as input
- Agent needs to **produce** videos as output

### Solution

Three-layer approach:

```
User's 3GB Video
    ↓
[FileManager.upload_file()]  ← Chunks at 50MB, progress tracking
    ↓
Server Storage (/tmp/openhands_files or S3)
    ↓
[send_message(text, file_attachments=[file_id])]
    ↓
Agent processes with file path
    ↓
Output video stored on server
    ↓
[FileManager.download_file(file_id)]  ← Stream with progress
    ↓
User's disk
```

## Usage

### 1. Upload Input Video

```python
from openhands_desktop.api.file_manager import FileManager

file_manager = FileManager(api_client._client)

def progress_callback(done, total):
    print(f"Uploaded {done}/{total} bytes")

file_id = await file_manager.upload_file(
    "/path/to/3gb_video.mp4",
    conversation_id="conv_123",
    on_progress=progress_callback
)
# file_id = "conv_123_abc123def456..."
```

### 2. Send Message with File

```python
await client.send_message(
    conversation_id="conv_123",
    text="Please analyze this video and extract text",
    file_attachments=[file_id]
)
```

### 3. Agent Processes It

Agent receives:
```json
{
  "type": "user",
  "content": [
    {"type": "text", "text": "Please analyze..."},
    {"type": "file", "file_id": "conv_123_abc123def456...", "path": "/tmp/openhands_files/conv_123_abc123def456..."}
  ]
}
```

Agent can now:
- Read video from `/tmp/openhands_files/conv_123_abc123def456...`
- Process it with ffmpeg, OpenCV, etc
- Write output to `/workspace/project/output_video.mp4`
- Return file path in response

### 4. Download Output Video

```python
output_file_id = "output_video"  # From agent response

def progress_callback(done, total):
    print(f"Downloaded {done}/{total} bytes")

local_path = await file_manager.download_file(
    output_file_id,
    output_path="/home/user/result.mp4",
    on_progress=progress_callback
)
```

## Key Points

### Upload Side
- **Chunking**: 50MB chunks (tunable)
- **Resume**: Failed chunks can be retried via upload_id
- **Hash**: File validated with SHA256
- **Progress**: Real-time callback for UI

### Download Side
- **Streaming**: HTTP Range requests, no buffering full file
- **Resume**: Can continue partial downloads
- **No WebSocket**: Pure HTTP streaming

### Agent Side
- Agent sees `file_id` → looks up actual path on disk
- No 3GB in JSON, no base64 encoding
- Native file I/O with full 3GB support

## Limitations & Future

### Current
- Stored on **local disk** (`/tmp/openhands_files`)
- Single-machine only
- No auth/permissions

### Next Steps
- **S3 Backend**: Replace local storage with S3 for multi-machine
- **Cleanup Policy**: Auto-delete old files after N days
- **Retry Logic**: Exponential backoff for failed chunks
- **Encryption**: For sensitive videos

## Testing

```bash
# Create 3GB test video
dd if=/dev/zero bs=1M count=3000 | ffmpeg -f lavfi -i color=black -i pipe: output.mp4

# Test upload
python3 -c """
import asyncio
from openhands_desktop.api.file_manager import FileManager
from openhands_desktop.api.client import AppServerClient

async def test():
    client = AppServerClient()
    fm = FileManager(client._client)
    fid = await fm.upload_file('output.mp4', 'test_conv')
    print(f'Uploaded: {fid}')

asyncio.run(test())
"""
```
