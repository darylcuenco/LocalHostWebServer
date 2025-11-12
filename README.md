# 🖼️ Thumbnail HTTP Server

A Python HTTP file server with image thumbnails and a modern file browsing interface. Perfect for viewing files from other devices on your network!

## Features

✨ **Image Thumbnails** - Auto-generates previews for image files (PNG, JPG, GIF, etc.)  
🎬 **Video Thumbnails** - Extracts frame from videos to show as preview  
📁 **Directory Browsing** - Clean, modern grid-based interface  
🏠 **Navigation** - Breadcrumb navigation for easy folder traversal  
📤 **File Upload** - Drag & drop or click to upload multiple files  
📊 **File Info** - Displays file sizes for all entries  
🎨 **File Icons** - Visual indicators for different file types (documents, archives, etc.)  
🌐 **Network Ready** - Bind to `0.0.0.0` to access from any device on your network  

### ⚡ Performance Optimizations
- 🚀 **Thumbnail Caching** - Generated thumbnails cached in memory (up to 500)
- 📦 **GZIP Compression** - HTML and text files compressed for faster transmission
- 🔄 **Concurrent Requests** - Uses ThreadingHTTPServer for simultaneous connections
- 📄 **Pagination** - Displays 50 items per page to reduce initial load time
- 🖼️ **Lazy Loading** - Images load with native lazy-loading attribute
- 💾 **Browser Caching** - Files cached client-side with 24-hour TTL

## Installation

1. Install Python 3.6 or higher
2. Install FFmpeg (for video thumbnails):
   - **Windows**: `choco install ffmpeg` or download from https://ffmpeg.org/download.html
   - **macOS**: `brew install ffmpeg`
   - **Linux**: `sudo apt-get install ffmpeg`
3. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Basic Usage (Current Directory)
```bash
python server.py
```
Server runs on `http://localhost:4098`, serving files from the current directory.

### Serve a Specific Folder
```bash
python server.py "C:\Users\Documents\Photos"
```
Serves the Photos folder on `http://localhost:4098`

### Custom Port
```bash
python server.py "C:\Users\Documents\Photos" 8000
```
Serves the folder on port 8000

### Network Access (Replace `http.server`)
```bash
python server.py "C:\path\to\folder" 4098 0.0.0.0
```
This replaces: `python -m http.server 4098 --bind 0.0.0.0`

Access from another device on your network:
- Find your computer's IP address (e.g., `192.168.1.100`)
- Go to `http://192.168.1.100:4098` in your browser

## Command Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `folder_path` | Folder to serve (can be relative or absolute path) | `.` (current directory) |
| `port` | Port number to run server on | `4098` |
| `bind_address` | IP address to bind to | `0.0.0.0` |

**Note:** If the first argument is a number, it's treated as the port (for backward compatibility).

### Examples

```bash
# Serve current directory on port 4098
python server.py

# Serve specific folder on default port
python server.py "C:\Users\Downloads"

# Serve specific folder on custom port
python server.py "D:\Media\Videos" 8080

# Serve specific folder on custom port, accessible from network
python server.py "C:\Shared\Files" 4098 0.0.0.0

# Serve current directory on custom port
python server.py 9000
```

## How It Works

1. **Thumbnail Generation** - The server uses Pillow (PIL) to generate image thumbnails on-the-fly
2. **Grid Layout** - Files and folders are displayed in a responsive grid
3. **File Type Detection** - Automatically detects file types and shows appropriate icons
4. **Download Support** - Click any file to download it directly

## Requirements

- Python 3.6+
- Pillow (for image thumbnail generation)
- FFmpeg (for video thumbnail extraction - optional but recommended)

## Performance Notes

The server includes several optimizations for fast network access:

- **Thumbnail Caching**: First load generates thumbnails (slower), subsequent requests use cached versions (instant)
- **Compression**: HTML responses are gzip-compressed, reducing size by 70-80%
- **Concurrent Connections**: Multiple users can access the server simultaneously without blocking
- **Pagination**: Directories with 50+ files show in pages to keep the page responsive
- **Native Lazy Loading**: Images use browser's lazy-loading, only loading when visible in viewport
- **Browser Caching**: Files cache for 24 hours on client side (includes ETag support)

### Expected Performance
- **First load**: 2-5s (generates thumbnails)
- **Subsequent loads**: <500ms (cached thumbnails + compression)
- **Video thumbnail extraction**: ~1-2s per video (FFmpeg)
- **File uploads**: Speed limited by network bandwidth, not server

### Network Speed Improvement Tips
1. Use **Pagination** - 50 items per page loads ~80% faster than 500 items
2. **Enable caching** - Revisiting folders uses cached thumbnails instantly
3. **Compression** - gzip reduces HTML by 75%, JSON/text by 80%
4. **Concurrent access** - Multiple users don't block each other

## Troubleshooting

**Q: I see emoji icons instead of thumbnails**  
A: Make sure Pillow is installed: `pip install -r requirements.txt`

## Troubleshooting

**Q: Video thumbnails aren't showing**  
A: Make sure FFmpeg is installed and in your PATH. Install from https://ffmpeg.org or use package manager

**Q: Pages load slowly on first visit**  
A: First load generates all thumbnails (especially with videos). Subsequent visits use cache. Reduce folder size to speed up.

**Q: Cache is using too much memory**  
A: Cache is limited to 500 items max. Reduce by editing `MAX_CACHE_SIZE` in server.py

**Q: Can't access from other devices**  
A: Make sure you're using `0.0.0.0` as the bind address and that your firewall allows the port

**Q: Still experiencing slow speeds?**  
A: 
- Check network bandwidth (test with `speedtest.net`)
- Use pagination - display only 50 items at a time
- Disable thumbnail generation for video-heavy folders (edit `ITEMS_PER_PAGE` to reduce load)
- Consider using a different device on the network to narrow down the issue

## License

MIT
