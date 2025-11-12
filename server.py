#!/usr/bin/env python3
"""
HTTP Server with Image Thumbnails and File Browsing (Optimized)
Serves files from a specified directory with caching, compression, and lazy-loading.
Usage: python server.py [folder_path] [port] [bind_address]
Examples: 
  python server.py                                  # Current directory, port 4098
  python server.py "C:\\Users\\Documents\\Photos"   # Specific folder, port 4098
  python server.py "C:\\Users\\Documents" 8000      # Specific folder, port 8000
  python server.py "C:\\Users\\Documents" 4098 0.0.0.0  # All parameters
"""

import http.server
import socketserver
import os
import sys
import base64
import mimetypes
from pathlib import Path
from urllib.parse import quote, unquote
import io
import subprocess
import gzip
import json
from threading import Lock
import zipfile
import tempfile

try:
    from PIL import Image
except ImportError:
    Image = None

# Parse command line arguments
SERVE_PATH = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else "."
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else (int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 4098)
BIND_ADDRESS = sys.argv[3] if len(sys.argv) > 3 else "0.0.0.0"

# Validate and resolve the serve path
if not os.path.isabs(SERVE_PATH):
    SERVE_PATH = os.path.abspath(SERVE_PATH)

if not os.path.exists(SERVE_PATH):
    print(f"❌ Error: Path does not exist: {SERVE_PATH}")
    sys.exit(1)

if not os.path.isdir(SERVE_PATH):
    print(f"❌ Error: Path is not a directory: {SERVE_PATH}")
    sys.exit(1)

# Optimization: In-memory thumbnail cache
THUMBNAIL_CACHE = {}
CACHE_LOCK = Lock()
MAX_CACHE_SIZE = 500  # Limit cache to prevent memory bloat

# Pagination settings
ITEMS_PER_PAGE = 50


class ThumbnailHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler with thumbnail support, caching, compression, and file upload."""

    def end_headers(self):
        """Add cache and compression headers."""
        # Add compression
        self.send_header('Content-Encoding', 'gzip')
        self.send_header('Cache-Control', 'public, max-age=3600')  # Cache for 1 hour
        super().end_headers()

    def send_response(self, code, message=None):
        """Override to add compression support."""
        super().send_response(code, message)

    def wfile_write_compressed(self, data):
        """Compress and write data to client."""
        if isinstance(data, str):
            data = data.encode('utf-8')
        compressed = gzip.compress(data, compresslevel=6)
        self.wfile.write(compressed)

    def do_POST(self):
        """Handle file uploads and downloads."""
        path = unquote(self.path)
        
        # Remove query string if present
        if '?' in path:
            path = path.split('?')[0]

        # Get target directory
        fs_path = os.path.normpath(os.path.join(SERVE_PATH, path.lstrip('/')))
        
        # Security check
        if not os.path.normpath(fs_path).startswith(os.path.normpath(SERVE_PATH)):
            self.send_error(403, "Access denied")
            return

        # Check if this is a download request
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b''
        
        # Parse the body to check if it's a download or upload
        body_str = body.decode('utf-8', errors='ignore')
        
        if 'files_to_download=' in body_str:
            # Handle file download
            self.handle_download(body_str, path)
            return
        
        # Handle file upload (multipart form data)
        if not os.path.isdir(fs_path):
            self.send_error(400, "Target must be a directory")
            return

        try:
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self.send_error(400, "Invalid content type")
                return

            # Extract boundary
            boundary = content_type.split('boundary=')[1].encode()
            
            # Parse multipart data
            parts = body.split(b'--' + boundary)
            uploaded_count = 0
            
            for part in parts[1:-1]:  # Skip first empty and last closing
                if b'filename=' not in part:
                    continue
                
                # Extract filename
                filename_start = part.find(b'filename="') + len(b'filename="')
                filename_end = part.find(b'"', filename_start)
                filename = part[filename_start:filename_end].decode('utf-8')
                
                # Extract file content
                content_start = part.find(b'\r\n\r\n') + 4
                content_end = part.rfind(b'\r\n')
                file_content = part[content_start:content_end]
                
                # Save file
                if filename:
                    file_path = os.path.join(fs_path, os.path.basename(filename))
                    with open(file_path, 'wb') as f:
                        f.write(file_content)
                    uploaded_count += 1
            
            # Send success response
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(f"✅ Successfully uploaded {uploaded_count} file(s)".encode())
            
        except Exception as e:
            self.send_error(500, f"Upload failed: {str(e)}")

    def handle_download(self, body_str, path):
        """Handle file download (single or multiple files as ZIP)."""
        try:
            # Parse the files to download
            files_section = body_str.split('files_to_download=')[1].split('&')[0] if 'files_to_download=' in body_str else ''
            from urllib.parse import unquote as url_unquote
            files_list = [url_unquote(f) for f in files_section.split(',') if f.strip()]
            
            if not files_list:
                self.send_error(400, "No files selected")
                return
            
            fs_path = os.path.normpath(os.path.join(SERVE_PATH, path.lstrip('/')))
            
            # Single file download
            if len(files_list) == 1:
                file_name = files_list[0]
                file_path = os.path.normpath(os.path.join(fs_path, file_name))
                
                # Security check
                if not os.path.normpath(file_path).startswith(os.path.normpath(SERVE_PATH)):
                    self.send_error(403, "Access denied")
                    return
                
                if os.path.isfile(file_path):
                    self.send_file_download(file_path, file_name)
                    return
            
            # Multiple files as ZIP
            self.send_zip_download(fs_path, files_list)
            
        except Exception as e:
            self.send_error(500, f"Download failed: {str(e)}")

    def send_file_download(self, file_path, file_name):
        """Send a single file for download."""
        try:
            file_size = os.path.getsize(file_path)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{file_name}"')
            self.send_header('Content-Length', str(file_size))
            self.end_headers()
            
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            self.send_error(500, f"Download failed: {str(e)}")

    def send_zip_download(self, base_path, files_list):
        """Create and send a ZIP file with multiple selected files."""
        try:
            # Create a temporary ZIP file
            temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            temp_zip_path = temp_zip.name
            temp_zip.close()
            
            with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_name in files_list:
                    file_path = os.path.normpath(os.path.join(base_path, file_name))
                    
                    # Security check
                    if not os.path.normpath(file_path).startswith(os.path.normpath(SERVE_PATH)):
                        continue
                    
                    if os.path.isfile(file_path):
                        # Add file to ZIP with relative path
                        zf.write(file_path, arcname=file_name)
            
            # Send the ZIP file
            file_size = os.path.getsize(temp_zip_path)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/zip')
            self.send_header('Content-Disposition', 'attachment; filename="download.zip"')
            self.send_header('Content-Length', str(file_size))
            self.end_headers()
            
            with open(temp_zip_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            
            # Clean up temp file
            os.remove(temp_zip_path)
            
        except Exception as e:
            self.send_error(500, f"ZIP creation failed: {str(e)}")

    def do_GET(self):
        """Handle GET requests."""
        path = unquote(self.path)
        
        # Extract page number from query string
        page = 1
        if '?' in path:
            query_string = path.split('?')[1]
            path = path.split('?')[0]
            # Parse page parameter
            for param in query_string.split('&'):
                if param.startswith('page='):
                    try:
                        page = int(param.split('=')[1])
                        if page < 1:
                            page = 1
                    except (ValueError, IndexError):
                        page = 1
        else:
            path = path.split('?')[0]

        # Check if path is a directory
        fs_path = os.path.normpath(os.path.join(SERVE_PATH, path.lstrip('/')))
        
        # Security check: ensure path is within serve directory
        if not os.path.normpath(fs_path).startswith(os.path.normpath(SERVE_PATH)):
            self.send_error(403, "Access denied")
            return

        if os.path.isdir(fs_path):
            self.list_directory(fs_path, page)
            return
        
        # For files, serve them directly
        if os.path.isfile(fs_path):
            self.send_file(fs_path)
            return
        
        # File not found
        self.send_error(404, "File not found")

    def send_file(self, file_path):
        """Send a file to the client with range request support for video seeking."""
        try:
            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type is None:
                mime_type = 'application/octet-stream'
            
            file_size = os.path.getsize(file_path)
            
            # Check for range request (video seeking)
            range_header = self.headers.get('Range')
            
            if range_header:
                # Parse Range header (e.g., "bytes=1000-2000")
                try:
                    range_start, range_end = range_header.replace('bytes=', '').split('-')
                    range_start = int(range_start) if range_start else 0
                    range_end = int(range_end) if range_end else file_size - 1
                    
                    # Validate range
                    if range_start >= file_size or range_end >= file_size:
                        range_start = 0
                        range_end = file_size - 1
                    
                    content_length = range_end - range_start + 1
                    
                    self.send_response(206)  # Partial Content
                    self.send_header('Content-type', mime_type)
                    self.send_header('Content-Length', str(content_length))
                    self.send_header('Content-Range', f'bytes {range_start}-{range_end}/{file_size}')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'public, max-age=86400')
                    self.end_headers()
                    
                    # Send requested range
                    with open(file_path, 'rb') as f:
                        f.seek(range_start)
                        remaining = content_length
                        while remaining > 0:
                            chunk_size = min(65536, remaining)
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except Exception:
                    # Fall back to full file send
                    self.send_full_file(file_path, file_size, mime_type)
            else:
                # Don't compress video files
                should_compress = mime_type in ['text/html', 'text/plain', 'text/css', 'application/json', 'application/javascript']
                
                self.send_response(200)
                self.send_header('Content-type', mime_type)
                
                if should_compress:
                    self.send_header('Content-Encoding', 'gzip')
                    self.end_headers()
                    with open(file_path, 'rb') as f:
                        compressed = gzip.compress(f.read(), compresslevel=6)
                        self.wfile.write(compressed)
                else:
                    self.send_header('Content-Length', str(file_size))
                    self.send_header('Accept-Ranges', 'bytes')  # Advertise range support
                    self.send_header('Cache-Control', 'public, max-age=86400')  # Cache for 24 hours
                    self.end_headers()
                    
                    self.send_full_file(file_path, file_size, mime_type)
        except Exception as e:
            self.send_error(500, f"Error serving file: {str(e)}")

    def send_full_file(self, file_path, file_size, mime_type):
        """Send complete file in chunks."""
        with open(file_path, 'rb') as f:
            remaining = file_size
            while remaining > 0:
                chunk_size = min(65536, remaining)  # 64KB chunks
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def list_directory(self, path, page=1):
        """Generate HTML directory listing with thumbnails and pagination."""
        try:
            entries = os.listdir(path)
        except PermissionError:
            self.send_error(403, "Permission denied")
            return

        entries.sort(key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))

        # Build the HTML
        rel_path = os.path.relpath(path, SERVE_PATH).replace('\\', '/')
        if rel_path == '.':
            rel_path = ''

        display_path = '/' + rel_path if rel_path else '/'

        html_parts = [
            '<!DOCTYPE html>',
            '<html>',
            '<head>',
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            '<title>File Browser - ' + display_path + '</title>',
            '<style>',
            'body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }',
            'h1 { color: #333; margin-bottom: 10px; }',
            '.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }',
            '.breadcrumb { margin-bottom: 20px; }',
            '.breadcrumb a { color: #0066cc; text-decoration: none; margin: 0 5px; }',
            '.breadcrumb a:hover { text-decoration: underline; }',
            '.upload-section { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }',
            '.upload-area { border: 2px dashed #0066cc; border-radius: 8px; padding: 30px; text-align: center; cursor: pointer; transition: background 0.3s; }',
            '.upload-area:hover { background: #f0f7ff; }',
            '.upload-area.dragover { background: #e3f2fd; border-color: #1976d2; }',
            '.upload-area p { margin: 0 0 10px 0; color: #666; }',
            '.file-input { display: none; }',
            '.upload-btn { background: #0066cc; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 14px; }',
            '.upload-btn:hover { background: #0052a3; }',
            '.upload-progress { margin-top: 10px; display: none; }',
            '.progress-bar { width: 100%; height: 4px; background: #e0e0e0; border-radius: 2px; overflow: hidden; }',
            '.progress-fill { height: 100%; background: #0066cc; width: 0%; transition: width 0.3s; }',
            '.upload-status { margin-top: 10px; padding: 10px; border-radius: 4px; display: none; }',
            '.upload-status.success { background: #e8f5e9; color: #2e7d32; }',
            '.upload-status.error { background: #ffebee; color: #c62828; }',
            '.container { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 20px; }',
            '.item { background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); transition: transform 0.2s, box-shadow 0.2s; cursor: pointer; }',
            '.item:hover { transform: translateY(-5px); box-shadow: 0 8px 16px rgba(0,0,0,0.15); }',
            '.item a { text-decoration: none; color: inherit; display: flex; flex-direction: column; height: 100%; }',
            '.thumbnail { width: 100%; height: 120px; background: #e8e8e8; display: flex; align-items: center; justify-content: center; font-size: 48px; overflow: hidden; }',
            '.thumbnail img { width: 100%; height: 100%; object-fit: cover; }',
            '.info { padding: 10px; flex: 1; display: flex; flex-direction: column; justify-content: space-between; }',
            '.name { font-weight: 500; word-break: break-word; font-size: 13px; }',
            '.size { font-size: 11px; color: #666; margin-top: 5px; }',
            '.directory .thumbnail { background: #e3f2fd; }',
            '.pagination { text-align: center; margin-top: 40px; padding: 20px; }',
            '.pagination a, .pagination span { display: inline-block; padding: 8px 12px; margin: 0 4px; border-radius: 4px; }',
            '.pagination a { background: #0066cc; color: white; text-decoration: none; cursor: pointer; }',
            '.pagination a:hover { background: #0052a3; }',
            '.pagination .current { background: #333; color: white; padding: 8px 12px; border-radius: 4px; }',
            '.pagination .disabled { color: #ccc; cursor: not-allowed; }',
            '.pagination-info { color: #666; font-size: 14px; margin-bottom: 10px; }',
            '.item.selected { background: #e3f2fd; box-shadow: 0 0 0 2px #0066cc; }',
            '.file-checkbox { margin-right: 8px; cursor: pointer; width: 18px; height: 18px; }',
            '.item-header { display: flex; align-items: center; padding: 10px; background: #f9f9f9; border-bottom: 1px solid #e0e0e0; }',
            '.modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 1000; align-items: center; justify-content: center; }',
            '.modal.show { display: flex; }',
            '.modal-content { background: white; border-radius: 8px; max-width: 90%; max-height: 90%; overflow: auto; position: relative; }',
            '.modal-header { padding: 15px; background: #333; color: white; display: flex; justify-content: space-between; align-items: center; border-radius: 8px 8px 0 0; }',
            '.modal-close { background: none; border: none; color: white; font-size: 24px; cursor: pointer; }',
            '.video-player { width: 100%; max-width: 800px; }',
            '.video-player video { width: 100%; height: auto; }',
            '</style>',
            '</head>',
            '<body>',
            '<div class="header">',
            '<h1>📂 ' + display_path + '</h1>',
            '</div>',
        ]

        # Breadcrumb navigation
        if rel_path:
            html_parts.append('<div class="breadcrumb">')
            html_parts.append('<a href="/">🏠 Home</a>')
            parts = rel_path.split('/')
            current = ''
            for part in parts:
                current += '/' + part if current else '/' + part
                html_parts.append(f' / <a href="{quote(current)}">{part}</a>')
            html_parts.append('</div>')

        # Upload section
        html_parts.extend([
            '<div class="upload-section">',
            '<form id="uploadForm" class="upload-area" ondrop="handleDrop(event)" ondragover="handleDragOver(event)" ondragleave="handleDragLeave(event)">',
            '<input type="file" id="fileInput" class="file-input" multiple onchange="handleFileSelect(event)">',
            '<p><strong>📁 Drag files here or click to select</strong></p>',
            '<p style="font-size: 12px; color: #999;">Select multiple files to upload</p>',
            '<button type="button" class="upload-btn" onclick="document.getElementById(\'fileInput\').click()">Choose Files</button>',
            '<div class="upload-progress" id="uploadProgress">',
            '<div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>',
            '</div>',
            '<div class="upload-status" id="uploadStatus"></div>',
            '</form>',
            '</div>',
            '<script>',
            'function handleDragOver(e) {',
            '  e.preventDefault();',
            '  e.stopPropagation();',
            '  document.getElementById("uploadForm").classList.add("dragover");',
            '}',
            'function handleDragLeave(e) {',
            '  e.preventDefault();',
            '  e.stopPropagation();',
            '  document.getElementById("uploadForm").classList.remove("dragover");',
            '}',
            'function handleDrop(e) {',
            '  e.preventDefault();',
            '  e.stopPropagation();',
            '  document.getElementById("uploadForm").classList.remove("dragover");',
            '  const files = e.dataTransfer.files;',
            '  uploadFiles(files);',
            '}',
            'function handleFileSelect(e) {',
            '  uploadFiles(e.target.files);',
            '}',
            'function uploadFiles(files) {',
            '  if (files.length === 0) return;',
            '  const formData = new FormData();',
            '  for (let i = 0; i < files.length; i++) {',
            '    formData.append("files", files[i]);',
            '  }',
            '  const progressDiv = document.getElementById("uploadProgress");',
            '  const statusDiv = document.getElementById("uploadStatus");',
            '  progressDiv.style.display = "block";',
            '  statusDiv.style.display = "none";',
            '  fetch(window.location.pathname, {',
            '    method: "POST",',
            '    body: formData',
            '  })',
            '  .then(response => response.text())',
            '  .then(data => {',
            '    progressDiv.style.display = "none";',
            '    statusDiv.style.display = "block";',
            '    statusDiv.className = "upload-status success";',
            '    statusDiv.textContent = data;',
            '    document.getElementById("fileInput").value = "";',
            '    setTimeout(() => location.reload(), 2000);',
            '  })',
            '  .catch(error => {',
            '    progressDiv.style.display = "none";',
            '    statusDiv.style.display = "block";',
            '    statusDiv.className = "upload-status error";',
            '    statusDiv.textContent = "❌ Upload failed: " + error;',
            '  });',
            '}',
            '</script>',
        ])

        # Add download section
        html_parts.extend([
            '<div class="download-section" id="downloadSection" style="display: none; background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">',
            '<div style="display: flex; justify-content: space-between; align-items: center;">',
            '<div>',
            '<input type="checkbox" id="selectAll" onchange="toggleSelectAll(this)"> <strong>Select All</strong>',
            '<span id="selectedCount" style="margin-left: 20px; color: #666;">0 selected</span>',
            '</div>',
            '<button class="upload-btn" onclick="downloadSelected()" style="background: #28a745;">⬇️ Download Selected</button>',
            '</div>',
            '</div>',
            '<script>',
            'const selectedFiles = new Set();',
            '',
            'function toggleSelectAll(checkbox) {',
            '  const checkboxes = document.querySelectorAll(".file-checkbox");',
            '  checkboxes.forEach(cb => {',
            '    cb.checked = checkbox.checked;',
            '    const item = cb.closest(".item");',
            '    if (checkbox.checked) {',
            '      selectedFiles.add(cb.value);',
            '      item.classList.add("selected");',
            '    } else {',
            '      selectedFiles.delete(cb.value);',
            '      item.classList.remove("selected");',
            '    }',
            '  });',
            '  updateSelectedCount();',
            '}',
            '',
            'function toggleFileSelect(checkbox) {',
            '  console.log("Toggling:", checkbox.value, "Checked:", checkbox.checked);',
            '  if (checkbox.checked) {',
            '    selectedFiles.add(checkbox.value);',
            '  } else {',
            '    selectedFiles.delete(checkbox.value);',
            '  }',
            '  console.log("Selected files:", Array.from(selectedFiles));',
            '  updateSelectedCount();',
            '}',
            '',
            'function updateSelectedCount() {',
            '  const count = selectedFiles.size;',
            '  document.getElementById("selectedCount").textContent = count + " selected";',
            '  const downloadSection = document.getElementById("downloadSection");',
            '  downloadSection.style.display = count > 0 ? "block" : "none";',
            '}',
            '',
            'function downloadSelected() {',
            '  if (selectedFiles.size === 0) {',
            '    alert("Please select files to download");',
            '    return;',
            '  }',
            '  console.log("Downloading:", Array.from(selectedFiles));',
            '  const form = document.createElement("form");',
            '  form.method = "POST";',
            '  form.action = window.location.pathname;',
            '  const filesInput = document.createElement("input");',
            '  filesInput.type = "hidden";',
            '  filesInput.name = "files_to_download";',
            '  filesInput.value = Array.from(selectedFiles).join(",");',
            '  form.appendChild(filesInput);',
            '  document.body.appendChild(form);',
            '  form.submit();',
            '  document.body.removeChild(form);',
            '}',
            '</script>',
        ])

        # Add video player modal
        html_parts.extend([
            '<div id="videoModal" class="modal">',
            '<div class="modal-content">',
            '<div class="modal-header">',
            '<span id="videoTitle"></span>',
            '<button class="modal-close" onclick="closeVideoPlayer()">&times;</button>',
            '</div>',
            '<div class="video-player">',
            '<video id="videoPlayer" controls preload="metadata" style="width: 100%; max-height: 70vh;">',
            '<source id="videoSource" src="" type="video/mp4">',
            'Your browser does not support the video tag.',
            '</video>',
            '</div>',
            '</div>',
            '</div>',
            '<script>',
            'function playVideo(filename, filepath) {',
            '  const videoModal = document.getElementById("videoModal");',
            '  const videoTitle = document.getElementById("videoTitle");',
            '  const videoSource = document.getElementById("videoSource");',
            '  const videoPlayer = document.getElementById("videoPlayer");',
            '  ',
            '  videoTitle.textContent = "▶️ " + filename;',
            '  videoSource.src = filepath;',
            '  videoSource.type = getMimeType(filename);',
            '  ',
            '  videoPlayer.load();',
            '  videoModal.classList.add("show");',
            '}',
            '',
            'function closeVideoPlayer() {',
            '  const videoModal = document.getElementById("videoModal");',
            '  const videoPlayer = document.getElementById("videoPlayer");',
            '  videoPlayer.pause();',
            '  videoModal.classList.remove("show");',
            '}',
            '',
            'function getMimeType(filename) {',
            '  const ext = filename.split(".").pop().toLowerCase();',
            '  const mimeTypes = {',
            '    "mp4": "video/mp4",',
            '    "webm": "video/webm",',
            '    "ogg": "video/ogg",',
            '    "mov": "video/quicktime",',
            '    "avi": "video/x-msvideo",',
            '    "mkv": "video/x-matroska",',
            '    "flv": "video/x-flv",',
            '    "wmv": "video/x-ms-wmv"',
            '  };',
            '  return mimeTypes[ext] || "video/mp4";',
            '}',
            '',
            'document.addEventListener("keydown", (e) => {',
            '  if (e.key === "Escape") closeVideoPlayer();',
            '});',
            '',
            'document.getElementById("videoModal").addEventListener("click", (e) => {',
            '  if (e.target.id === "videoModal") closeVideoPlayer();',
            '});',
            '</script>',
        ])

        html_parts.append('<div class="container">')

        # Add parent directory link
        if rel_path:
            parent = os.path.dirname(path)
            parent_rel = os.path.relpath(parent, SERVE_PATH).replace('\\', '/')
            if parent_rel == '.':
                parent_url = '/'
            else:
                parent_url = '/' + parent_rel
            html_parts.append(
                f'<div class="item directory"><a href="{quote(parent_url)}">'
                f'<div class="thumbnail">⬆️</div><div class="info"><div class="name">..</div></div></a></div>'
            )

        # Pagination: split entries into pages
        start_idx = (page - 1) * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        paginated_entries = entries[start_idx:end_idx]
        total_pages = (len(entries) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

        # Add directory entries
        for idx, entry in enumerate(paginated_entries):
            full_path = os.path.join(path, entry)
            rel = os.path.relpath(full_path, SERVE_PATH).replace('\\', '/')
            url = '/' + rel if rel != '.' else '/'

            if os.path.isdir(full_path):
                html_parts.append(
                    f'<div class="item directory"><a href="{quote(url)}">'
                    f'<div class="thumbnail">📁</div><div class="info"><div class="name">{entry}</div></div></a></div>'
                )
            else:
                size = os.path.getsize(full_path)
                size_str = format_size(size)
                thumbnail_html = get_thumbnail_html(full_path, entry)
                
                # Check if it's a video file
                mime_type, _ = mimetypes.guess_type(full_path)
                is_video = mime_type and mime_type.startswith('video/')
                
                item_id = f"item-{idx}-{page}"
                encoded_entry = entry.replace('"', '&quot;').replace("'", "&#39;")
                
                # Create onclick handler - play video for videos, open in new tab for other viewables
                if is_video:
                    onclick_handler = f'onclick="playVideo(\'{encoded_entry}\', \'{quote(url)}\'); event.stopPropagation();"'
                    link_content = f'{thumbnail_html}<div class="info"><div class="name"></div><div class="size">{size_str}</div></div>'
                else:
                    is_viewable = is_viewable_file(entry)
                    target = ' target="_blank"' if is_viewable else ''
                    onclick_handler = ''
                    link_content = f'{thumbnail_html}<div class="info"><div class="name"></div><div class="size">{size_str}</div></div>'
                
                html_parts.append(
                    f'<div class="item" id="{item_id}">'
                    f'<div class="item-header" style="position: relative; z-index: 10;">'
                    f'<input type="checkbox" class="file-checkbox" value="{encoded_entry}" onchange="toggleFileSelect(this); document.getElementById(\'{item_id}\').classList.toggle(\'selected\');">'
                    f'<span style="flex: 1; font-size: 12px; margin-left: 5px;">{entry}</span>'
                    f'</div>'
                    f'<a href="{quote(url) if not is_video else "#"}" {target if not is_video else ""} {onclick_handler}>'
                    f'{link_content}</a>'
                    f'</div>'
                )

        html_parts.extend([
            '</div>',
        ])

        # Add pagination controls
        if total_pages > 1:
            html_parts.append('<div class="pagination">')
            html_parts.append(f'<div class="pagination-info">Page {page} of {total_pages} • Showing {len(paginated_entries)} of {len(entries)} items</div>')
            
            # Previous button
            if page > 1:
                html_parts.append(f'<a href="{quote(display_path)}?page={page - 1}">← Previous</a>')
            else:
                html_parts.append('<span class="disabled">← Previous</span>')
            
            # Page numbers
            start_page = max(1, page - 2)
            end_page = min(total_pages, page + 2)
            
            if start_page > 1:
                html_parts.append(f'<a href="{quote(display_path)}?page=1">1</a>')
                if start_page > 2:
                    html_parts.append('<span>...</span>')
            
            for p in range(start_page, end_page + 1):
                if p == page:
                    html_parts.append(f'<span class="current">{p}</span>')
                else:
                    html_parts.append(f'<a href="{quote(display_path)}?page={p}">{p}</a>')
            
            if end_page < total_pages:
                if end_page < total_pages - 1:
                    html_parts.append('<span>...</span>')
                html_parts.append(f'<a href="{quote(display_path)}?page={total_pages}">{total_pages}</a>')
            
            # Next button
            if page < total_pages:
                html_parts.append(f'<a href="{quote(display_path)}?page={page + 1}">Next →</a>')
            else:
                html_parts.append('<span class="disabled">Next →</span>')
            
            html_parts.append('</div>')
        else:
            html_parts.append(f'<div style="text-align: center; margin-top: 40px; color: #666;">Showing {len(entries)} item{"s" if len(entries) != 1 else ""}</div>')

        html_parts.extend([
            '</body>',
            '</html>',
        ])

        html = '\n'.join(html_parts)
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('Content-Encoding', 'gzip')
        compressed_html = gzip.compress(html.encode('utf-8'), compresslevel=6)
        self.send_header('Content-Length', str(len(compressed_html)))
        self.end_headers()
        self.wfile.write(compressed_html)
        self.wfile.write(html.encode('utf-8'))

    def end_headers(self):
        """Add custom headers."""
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()


def get_thumbnail_html(file_path, filename):
    """Generate HTML for file thumbnail (with caching)."""
    cache_key = f"{file_path}:{os.path.getmtime(file_path)}"
    
    # Check cache first
    with CACHE_LOCK:
        if cache_key in THUMBNAIL_CACHE:
            return THUMBNAIL_CACHE[cache_key]
    
    result = None
    mime_type, _ = mimetypes.guess_type(file_path)
    
    if mime_type and mime_type.startswith('image/'):
        if Image:
            try:
                img = Image.open(file_path)
                img.thumbnail((150, 120), Image.Resampling.LANCZOS)
                buffered = io.BytesIO()
                img.save(buffered, format='PNG')
                img_str = base64.b64encode(buffered.getvalue()).decode()
                result = f'<div class="thumbnail" data-src="data:image/png;base64,{img_str}"><img loading="lazy" src="data:image/png;base64,{img_str}" alt="{filename}"></div>'
            except Exception:
                result = '<div class="thumbnail">🖼️</div>'
        else:
            result = '<div class="thumbnail">🖼️</div>'
    
    # Video thumbnails
    elif mime_type and mime_type.startswith('video/'):
        video_thumb = extract_video_thumbnail(file_path)
        if video_thumb:
            result = f'<div class="thumbnail" data-src="data:image/png;base64,{video_thumb}"><img loading="lazy" src="data:image/png;base64,{video_thumb}" alt="{filename}"></div>'
        else:
            result = '<div class="thumbnail">🎬</div>'
    
    # File type icons
    elif mime_type:
        if mime_type.startswith('audio/'):
            result = '<div class="thumbnail">🎵</div>'
        elif mime_type == 'application/pdf':
            result = '<div class="thumbnail">📄</div>'
        elif mime_type.startswith('text/'):
            result = '<div class="thumbnail">📝</div>'
        elif 'archive' in mime_type or 'zip' in mime_type or 'rar' in mime_type:
            result = '<div class="thumbnail">📦</div>'
    
    if not result:
        result = '<div class="thumbnail">📁</div>'
    
    # Cache the result
    with CACHE_LOCK:
        if len(THUMBNAIL_CACHE) < MAX_CACHE_SIZE:
            THUMBNAIL_CACHE[cache_key] = result
    
    return result


def extract_video_thumbnail(video_path, timestamp=2):
    """Extract a thumbnail from a video file using FFmpeg."""
    try:
        # Try to use ffmpeg to extract a frame
        temp_file = os.path.join(os.path.dirname(video_path), f".thumb_{os.getpid()}.png")
        
        # Use ffmpeg to extract frame at 2 seconds
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-ss', str(timestamp),
            '-vframes', '1',
            '-vf', 'scale=150:120:force_original_aspect_ratio=decrease,pad=150:120:(ow-iw)/2:(oh-ih)/2',
            '-y',  # Overwrite without asking
            temp_file
        ]
        
        # Run silently
        with open(os.devnull, 'w') as devnull:
            subprocess.run(cmd, stdout=devnull, stderr=devnull, timeout=5)
        
        if os.path.exists(temp_file):
            try:
                with open(temp_file, 'rb') as f:
                    img_data = f.read()
                    img_str = base64.b64encode(img_data).decode()
                    return img_str
            finally:
                # Clean up temp file
                try:
                    os.remove(temp_file)
                except:
                    pass
    except Exception:
        pass
    
    return None


def is_viewable_file(filename):
    """Check if a file is viewable in the browser (not downloaded)."""
    mime_type, _ = mimetypes.guess_type(filename)
    if not mime_type:
        return False
    
    # Viewable file types
    viewable_types = [
        'image/',  # All image types
        'video/',  # All video types
        'audio/',  # All audio types
        'application/pdf',
        'text/',   # Text files
        'application/json',
    ]
    
    for viewable in viewable_types:
        if mime_type.startswith(viewable) or mime_type == viewable:
            return True
    
    return False


def format_size(bytes_size):
    """Format bytes to human readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}".rstrip('0').rstrip('.')
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"


if __name__ == '__main__':
    handler = ThumbnailHTTPRequestHandler
    
    # Use ThreadingTCPServer for concurrent request handling
    with socketserver.ThreadingTCPServer((BIND_ADDRESS, PORT), handler) as httpd:
        print(f"🚀 Server running on http://{BIND_ADDRESS}:{PORT}")
        print(f"📂 Serving files from: {SERVE_PATH}")
        print(f"⚡ Optimizations: Caching, Compression, Threading, Pagination")
        print(f"📊 Cache size limit: {MAX_CACHE_SIZE} thumbnails | Items per page: {ITEMS_PER_PAGE}")
        print(f"⏹️  Press Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n✋ Server stopped")
