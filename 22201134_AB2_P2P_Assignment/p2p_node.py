import os
import socket
import threading
import uuid

import protocol


class Peer:
    """One connected remote peer and the socket used to talk to it."""

    def __init__(self, peer_id, peer_name, sock, address, listen_port=None):
        self.peer_id = peer_id
        self.peer_name = peer_name
        self.sock = sock
        self.address = address          # (ip, ephemeral_port) of the socket
        self.listen_port = listen_port  # the port the remote peer listens on
        self.send_lock = threading.Lock()  # one writer at a time per socket

    def display(self):
        """Short label used in the connected-peer list."""
        port = self.listen_port if self.listen_port else self.address[1]
        return f"{self.peer_name} [{self.peer_id}]  {self.address[0]}:{port}"


class P2PNode:
    """
    A single peer in the network.

    Callbacks let the user interface react to events without
    the networking layer knowing anything about Tkinter:

        on_log(text)            - a line for the event log
        on_peers_changed()      - the connected-peer list changed
        on_text(peer, message)  - a text message arrived
        on_file(peer, path)     - a file finished downloading
    """

    def __init__(self, name, port, download_dir="downloads",
                 on_log=None, on_peers_changed=None,
                 on_text=None, on_file=None):
        self.name = name
        self.port = int(port)
        self.peer_id = uuid.uuid4().hex[:8]   # short unique id, e.g. a83f21c4
        self.download_dir = download_dir

        self.server_socket = None
        self.running = False

        # peer_id -> Peer
        self.peers = {}
        self.peers_lock = threading.Lock()

        self.on_log = on_log or (lambda msg: None)
        self.on_peers_changed = on_peers_changed or (lambda: None)
        self.on_text = on_text or (lambda peer, msg: None)
        self.on_file = on_file or (lambda peer, path: None)

        os.makedirs(self.download_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------

    def log(self, message):
        self.on_log(message)

    # ------------------------------------------------------------------
    # Server role: start listening and accept connections
    # ------------------------------------------------------------------

    def start(self):
        """
        Create the server socket, bind it, and start the accept loop
        in a background thread.
        """
        if self.running:
            self.log("[SYSTEM] Peer is already running.")
            return False

        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Allow the port to be reused immediately after a restart.
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(("0.0.0.0", self.port))
            self.server_socket.listen(5)
        except OSError as exc:
            self.log(f"[ERROR] Could not start peer on port {self.port}: {exc}")
            self.server_socket = None
            return False

        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.log(f"[SYSTEM] Peer started: {self.name} [{self.peer_id}] "
                 f"on port {self.port}")
        return True

    def _accept_loop(self):
        """Wait for incoming connections; hand each one to its own thread."""
        while self.running:
            try:
                connection, address = self.server_socket.accept()
            except OSError:
                # Socket was closed by stop(); leave the loop quietly.
                break

            threading.Thread(
                target=self._handle_incoming,
                args=(connection, address),
                daemon=True,
            ).start()

    def _handle_incoming(self, connection, address):
        """
        An inbound peer connected to us. It sends HELLO first; we reply
        with HELLO_ACK and then run the normal receive loop.
        """
        try:
            message = protocol.recv_message(connection)
            if not message or message.get("type") != protocol.MSG_HELLO:
                self.log(f"[ERROR] Bad handshake from {address[0]}:{address[1]}")
                connection.close()
                return

            peer = Peer(
                peer_id=message.get("peer_id", "unknown"),
                peer_name=message.get("peer_name", "unknown"),
                sock=connection,
                address=address,
                listen_port=message.get("port"),
            )

            protocol.send_message(
                connection,
                protocol.make_hello_ack(self.peer_id, self.name, self.port),
            )

            self._register_peer(peer)
            self.log(f"[SYSTEM] Connected to {peer.peer_name}")
            self._receive_loop(peer)

        except (OSError, ValueError) as exc:
            self.log(f"[ERROR] Incoming connection failed: {exc}")
            try:
                connection.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Client role: connect out to another peer
    # ------------------------------------------------------------------

    def connect_to(self, ip, port):
        """
        Connect to a remote peer, send HELLO, wait for HELLO_ACK,
        then start a receive thread for that connection.
        """
        if not self.running:
            self.log("[ERROR] Start your peer before connecting to others.")
            return False

        try:
            port = int(port)
        except (TypeError, ValueError):
            self.log(f"[ERROR] Invalid port: {port}")
            return False

        if not (0 < port < 65536):
            self.log(f"[ERROR] Port out of range: {port}")
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(8)
            sock.connect((ip, port))
            sock.settimeout(None)

            protocol.send_message(
                sock, protocol.make_hello(self.peer_id, self.name, self.port)
            )
            reply = protocol.recv_message(sock)

            if not reply or reply.get("type") != protocol.MSG_HELLO_ACK:
                self.log(f"[ERROR] No handshake reply from {ip}:{port}")
                sock.close()
                return False

            peer = Peer(
                peer_id=reply.get("peer_id", "unknown"),
                peer_name=reply.get("peer_name", "unknown"),
                sock=sock,
                address=(ip, port),
                listen_port=reply.get("port", port),
            )

            self._register_peer(peer)
            self.log(f"[SYSTEM] Connected to {peer.peer_name} ({ip}:{port})")
            threading.Thread(target=self._receive_loop, args=(peer,),
                             daemon=True).start()
            return True

        except socket.gaierror:
            self.log(f"[ERROR] Invalid IP address: {ip}")
        except socket.timeout:
            self.log(f"[ERROR] Connection to {ip}:{port} timed out")
        except ConnectionRefusedError:
            self.log(f"[ERROR] Connection failed: Connection refused ({ip}:{port})")
        except OSError as exc:
            self.log(f"[ERROR] Connection failed: {exc}")
        return False

    # ------------------------------------------------------------------
    # Peer bookkeeping
    # ------------------------------------------------------------------

    def _register_peer(self, peer):
        with self.peers_lock:
            self.peers[peer.peer_id] = peer
        self.on_peers_changed()

    def _remove_peer(self, peer):
        with self.peers_lock:
            self.peers.pop(peer.peer_id, None)
        try:
            peer.sock.close()
        except OSError:
            pass
        self.on_peers_changed()

    def get_peers(self):
        with self.peers_lock:
            return list(self.peers.values())

    def get_peer(self, peer_id):
        with self.peers_lock:
            return self.peers.get(peer_id)

    # ------------------------------------------------------------------
    # Receive loop: one per connected peer
    # ------------------------------------------------------------------

    def _receive_loop(self, peer):
        """
        Read framed messages from this peer until it disconnects.

        A disconnecting peer must never crash the application; it is simply
        removed from the list and reported in the log.
        """
        try:
            while self.running:
                message = protocol.recv_message(peer.sock)
                if message is None:
                    break  # peer closed the connection

                msg_type = message.get("type")

                if msg_type == protocol.MSG_TEXT:
                    text = message.get("message", "")
                    self.log(f"{peer.peer_name} -> You: {text}")
                    self.on_text(peer, text)

                elif msg_type == protocol.MSG_FILE:
                    self._receive_file(peer, message)

                else:
                    self.log(f"[SYSTEM] Unknown message type from "
                             f"{peer.peer_name}: {msg_type}")

        except (ConnectionResetError, ConnectionAbortedError):
            self.log(f"[SYSTEM] {peer.peer_name} disconnected unexpectedly.")
        except (OSError, ValueError) as exc:
            self.log(f"[ERROR] Connection to {peer.peer_name} lost: {exc}")
        finally:
            self._remove_peer(peer)
            self.log(f"[SYSTEM] Disconnected from {peer.peer_name}")

    # ------------------------------------------------------------------
    # Sending: text
    # ------------------------------------------------------------------

    def send_text(self, peer_id, text):
        peer = self.get_peer(peer_id)
        if peer is None:
            self.log("[ERROR] Select a connected peer first.")
            return False
        if not text:
            self.log("[ERROR] Cannot send an empty message.")
            return False

        try:
            with peer.send_lock:
                protocol.send_message(
                    peer.sock,
                    protocol.make_text(self.peer_id, self.name, text),
                )
            self.log(f"You -> {peer.peer_name}: {text}")
            return True
        except OSError as exc:
            self.log(f"[ERROR] Failed to send message: {exc}")
            self._remove_peer(peer)
            return False

    # ------------------------------------------------------------------
    # Sending: files (metadata first, then raw bytes in chunks)
    # ------------------------------------------------------------------

    def send_file(self, peer_id, filepath):
        """
        Send any ordinary file as binary data.

        Steps:
            1. Validate the file.
            2. Send the file metadata (filename + filesize) as a JSON message.
            3. Stream the raw bytes in 64 KB chunks.

        The file is never fully loaded into memory, so large videos are fine.
        """
        peer = self.get_peer(peer_id)
        if peer is None:
            self.log("[ERROR] Select a connected peer first.")
            return False

        if not filepath or not os.path.isfile(filepath):
            self.log(f"[ERROR] File does not exist: {filepath}")
            return False

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        if filesize <= 0:
            self.log(f"[ERROR] Invalid file size for {filename}")
            return False

        try:
            with peer.send_lock:
                # Stage 1: metadata
                protocol.send_message(
                    peer.sock,
                    protocol.make_file_meta(self.peer_id, self.name,
                                            filename, filesize),
                )
                # Stage 2: raw bytes, in chunks
                sent = 0
                with open(filepath, "rb") as handle:
                    while True:
                        chunk = handle.read(protocol.CHUNK_SIZE)
                        if not chunk:
                            break
                        peer.sock.sendall(chunk)
                        sent += len(chunk)

            if sent != filesize:
                self.log(f"[ERROR] Incomplete send of {filename} "
                         f"({sent}/{filesize} bytes)")
                return False

            self.log(f"You -> {peer.peer_name}: File sent: {filename} "
                     f"({filesize} bytes)")
            return True

        except OSError as exc:
            self.log(f"[ERROR] Failed to send file: {exc}")
            self._remove_peer(peer)
            return False

    def _receive_file(self, peer, metadata):
        """
        Read exactly `filesize` bytes from the connection and write them
        into the downloads directory.

        The size from the metadata is what tells us when the transfer is
        complete -- one recv() call is NOT one file.
        """
        filename = os.path.basename(str(metadata.get("filename", "received.bin")))
        filesize = metadata.get("filesize", 0)

        if not isinstance(filesize, int) or filesize <= 0:
            self.log(f"[ERROR] Invalid file size for {filename}")
            return

        save_path = self._unique_path(os.path.join(self.download_dir, filename))

        remaining = filesize
        with open(save_path, "wb") as handle:
            while remaining > 0:
                chunk = peer.sock.recv(min(protocol.CHUNK_SIZE, remaining))
                if not chunk:
                    # Peer vanished mid-transfer.
                    raise ConnectionResetError(
                        "connection closed during file transfer"
                    )
                handle.write(chunk)
                remaining -= len(chunk)

        self.log(f"{peer.peer_name} -> You: File received: {filename} "
                 f"({filesize} bytes)")
        self.on_file(peer, save_path)

    @staticmethod
    def _unique_path(path):
        """Avoid overwriting an existing download: photo.jpg -> photo_1.jpg"""
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        counter = 1
        while os.path.exists(f"{base}_{counter}{ext}"):
            counter += 1
        return f"{base}_{counter}{ext}"

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def stop(self):
        """Close every peer connection and the listening socket."""
        if not self.running:
            return

        self.running = False

        for peer in self.get_peers():
            try:
                peer.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                peer.sock.close()
            except OSError:
                pass

        with self.peers_lock:
            self.peers.clear()

        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None

        self.on_peers_changed()
        self.log("[SYSTEM] Peer stopped.")
