import json
import struct

# Number of bytes used for the length prefix of every JSON message.
HEADER_SIZE = 4

# Size of each chunk used when streaming file bytes (64 KB).
CHUNK_SIZE = 64 * 1024

# Message type constants.
MSG_HELLO = "hello"
MSG_HELLO_ACK = "hello_ack"
MSG_TEXT = "text"
MSG_FILE = "file"


# ---------------------------------------------------------------------------
# Low-level send / receive helpers
# ---------------------------------------------------------------------------

def recv_exactly(sock, num_bytes):
    """
    Read exactly num_bytes from the socket.

    recv() is allowed to return fewer bytes than requested, so we loop until
    we have everything. Returns None if the peer closed the connection before
    all the bytes arrived.
    """
    buffer = bytearray()
    while len(buffer) < num_bytes:
        chunk = sock.recv(min(CHUNK_SIZE, num_bytes - len(buffer)))
        if not chunk:
            # Empty bytes means the other side closed the connection.
            return None
        buffer.extend(chunk)
    return bytes(buffer)


def send_message(sock, message_dict):
    """
    Encode a Python dictionary as JSON and send it with a 4-byte length prefix.
    """
    payload = json.dumps(message_dict).encode("utf-8")
    header = struct.pack("!I", len(payload))  # !I = big-endian unsigned int
    sock.sendall(header + payload)


def recv_message(sock):
    """
    Receive one complete framed JSON message.

    Returns the decoded dictionary, or None if the connection was closed.
    """
    header = recv_exactly(sock, HEADER_SIZE)
    if header is None:
        return None

    (length,) = struct.unpack("!I", header)

    # Guard against an absurd length (corrupt or hostile peer).
    if length <= 0 or length > 10 * 1024 * 1024:
        raise ValueError(f"Invalid message length received: {length}")

    payload = recv_exactly(sock, length)
    if payload is None:
        return None

    return json.loads(payload.decode("utf-8"))


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def make_hello(peer_id, peer_name, port):
    """Build the HELLO handshake message."""
    return {
        "type": MSG_HELLO,
        "peer_id": peer_id,
        "peer_name": peer_name,
        "port": port,
    }


def make_hello_ack(peer_id, peer_name, port):
    """Build the HELLO acknowledgement message."""
    return {
        "type": MSG_HELLO_ACK,
        "peer_id": peer_id,
        "peer_name": peer_name,
        "port": port,
    }


def make_text(sender_id, sender_name, message):
    """Build a text message."""
    return {
        "type": MSG_TEXT,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "message": message,
    }


def make_file_meta(sender_id, sender_name, filename, filesize):
    """
    Build the file metadata message.

    The raw file bytes are sent immediately after this message.
    """
    return {
        "type": MSG_FILE,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "filename": filename,
        "filesize": filesize,
    }
