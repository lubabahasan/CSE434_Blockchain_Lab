import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from p2p_node import P2PNode

DOWNLOAD_DIR = "downloads"


class P2PApp:
    def __init__(self, root):
        self.root = root
        self.root.title("UAP P2P Network")
        self.root.geometry("980x620")
        self.root.minsize(760, 520)

        self.node = None
        self.log_queue = queue.Queue()
        self.peer_ids = []   # parallel to the listbox rows

        self._build_ui()
        self._drain_log_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Interface construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # --- My Peer -------------------------------------------------
        my_peer = ttk.LabelFrame(self.root, text="My Peer")
        my_peer.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(my_peer, text="Name:").grid(row=0, column=0, sticky="w", **pad)
        self.name_entry = ttk.Entry(my_peer, width=18)
        self.name_entry.insert(0, "Alice")
        self.name_entry.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(my_peer, text="Port:").grid(row=0, column=2, sticky="w", **pad)
        self.port_entry = ttk.Entry(my_peer, width=10)
        self.port_entry.insert(0, "5000")
        self.port_entry.grid(row=0, column=3, sticky="w", **pad)

        self.start_btn = ttk.Button(my_peer, text="Start Peer",
                                    command=self.start_peer)
        self.start_btn.grid(row=0, column=4, **pad)

        self.stop_btn = ttk.Button(my_peer, text="Stop",
                                   command=self.stop_peer, state="disabled")
        self.stop_btn.grid(row=0, column=5, **pad)

        self.identity_label = ttk.Label(my_peer, text="Peer not started.")
        self.identity_label.grid(row=1, column=0, columnspan=6,
                                 sticky="w", padx=6, pady=(0, 6))

        # --- Connect to another peer ---------------------------------
        connect = ttk.LabelFrame(self.root, text="Connect to Another Peer")
        connect.pack(fill="x", padx=8, pady=4)

        ttk.Label(connect, text="IP:").grid(row=0, column=0, sticky="w", **pad)
        self.ip_entry = ttk.Entry(connect, width=18)
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(connect, text="Port:").grid(row=0, column=2, sticky="w", **pad)
        self.remote_port_entry = ttk.Entry(connect, width=10)
        self.remote_port_entry.insert(0, "5001")
        self.remote_port_entry.grid(row=0, column=3, sticky="w", **pad)

        ttk.Button(connect, text="Connect",
                   command=self.connect_peer).grid(row=0, column=4, **pad)

        # --- Peers + log ---------------------------------------------
        middle = ttk.Frame(self.root)
        middle.pack(fill="both", expand=True, padx=8, pady=4)

        peers_frame = ttk.LabelFrame(middle, text="Connected Peers")
        peers_frame.pack(side="left", fill="y")

        self.peer_list = tk.Listbox(peers_frame, width=36, exportselection=False)
        self.peer_list.pack(fill="both", expand=True, padx=6, pady=6)

        log_frame = ttk.LabelFrame(middle, text="Messages / Events")
        log_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self.log_text = tk.Text(log_frame, wrap="word", state="disabled",
                                font=("Consolas", 10))
        self.log_text.pack(side="left", fill="both", expand=True,
                           padx=(6, 0), pady=6)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # --- Send text -----------------------------------------------
        text_frame = ttk.LabelFrame(self.root, text="Send Text")
        text_frame.pack(fill="x", padx=8, pady=4)

        self.message_entry = ttk.Entry(text_frame)
        self.message_entry.pack(side="left", fill="x", expand=True,
                                padx=6, pady=6)
        self.message_entry.bind("<Return>", lambda event: self.send_text())

        ttk.Button(text_frame, text="Send",
                   command=self.send_text).pack(side="left", padx=6, pady=6)

        # --- Send file -----------------------------------------------
        file_frame = ttk.LabelFrame(self.root, text="Send File")
        file_frame.pack(fill="x", padx=8, pady=(4, 8))

        ttk.Label(file_frame,
                  text="Text, image, audio, video, PDF, ZIP, etc."
                  ).pack(side="left", padx=6, pady=6)

        ttk.Button(file_frame, text="Choose File && Send",
                   command=self.send_file).pack(side="right", padx=6, pady=6)

        # --- Status bar ----------------------------------------------
        self.status = ttk.Label(self.root, text="Ready.", relief="sunken",
                                anchor="w")
        self.status.pack(fill="x", side="bottom")

    # ------------------------------------------------------------------
    # Thread-safe logging
    # ------------------------------------------------------------------

    def enqueue_log(self, message):
        """Called from network threads. Only queues; never touches widgets."""
        self.log_queue.put(message)

    def _drain_log_queue(self):
        """Runs on the GUI thread every 100 ms and flushes queued log lines."""
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
            self.status.configure(text=message)
        self.root.after(100, self._drain_log_queue)

    def refresh_peers(self):
        """Rebuild the connected-peer listbox (GUI thread only)."""
        selected = self.selected_peer_id()
        self.peer_list.delete(0, "end")
        self.peer_ids = []

        if self.node:
            for peer in self.node.get_peers():
                self.peer_list.insert("end", peer.display())
                self.peer_ids.append(peer.peer_id)

        if selected in self.peer_ids:
            index = self.peer_ids.index(selected)
            self.peer_list.selection_set(index)

    def selected_peer_id(self):
        selection = self.peer_list.curselection()
        if not selection:
            return None
        return self.peer_ids[selection[0]]

    # ------------------------------------------------------------------
    # Button actions
    # ------------------------------------------------------------------

    def start_peer(self):
        name = self.name_entry.get().strip()
        port_text = self.port_entry.get().strip()

        if not name:
            messagebox.showerror("Invalid name", "Please enter a peer name.")
            return

        try:
            port = int(port_text)
        except ValueError:
            messagebox.showerror("Invalid port",
                                 f"'{port_text}' is not a valid port number.")
            return

        if not (1024 <= port <= 65535):
            messagebox.showerror("Invalid port",
                                 "Use a port between 1024 and 65535.")
            return

        self.node = P2PNode(
            name=name,
            port=port,
            download_dir=DOWNLOAD_DIR,
            on_log=self.enqueue_log,
            on_peers_changed=lambda: self.root.after(0, self.refresh_peers),
        )

        if not self.node.start():
            self.node = None
            return

        self.identity_label.configure(
            text=f"{name} | ID: {self.node.peer_id} | Port: {port}"
        )
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.name_entry.configure(state="disabled")
        self.port_entry.configure(state="disabled")

    def stop_peer(self):
        if self.node:
            self.node.stop()
            self.node = None

        self.refresh_peers()
        self.identity_label.configure(text="Peer not started.")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.name_entry.configure(state="normal")
        self.port_entry.configure(state="normal")

    def connect_peer(self):
        if not self.node:
            messagebox.showwarning("Peer not running",
                                   "Start your peer before connecting.")
            return

        ip = self.ip_entry.get().strip()
        port = self.remote_port_entry.get().strip()

        if not ip:
            messagebox.showerror("Invalid IP", "Please enter an IP address.")
            return

        self.node.connect_to(ip, port)

    def send_text(self):
        if not self.node:
            messagebox.showwarning("Peer not running", "Start your peer first.")
            return

        peer_id = self.selected_peer_id()
        if peer_id is None:
            messagebox.showwarning("No peer selected",
                                   "Select a peer from the list first.")
            return

        message = self.message_entry.get().strip()
        if not message:
            return

        if self.node.send_text(peer_id, message):
            self.message_entry.delete(0, "end")

    def send_file(self):
        if not self.node:
            messagebox.showwarning("Peer not running", "Start your peer first.")
            return

        peer_id = self.selected_peer_id()
        if peer_id is None:
            messagebox.showwarning("No peer selected",
                                   "Select a peer from the list first.")
            return

        filepath = filedialog.askopenfilename(title="Choose a file to send")
        if not filepath:
            return

        self.node.send_file(peer_id, filepath)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _on_close(self):
        if self.node:
            self.node.stop()
        self.root.destroy()


def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    P2PApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
