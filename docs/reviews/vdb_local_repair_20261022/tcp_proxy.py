"""Forward the runner's loopback Milvus port to the host Standalone."""

import socket
import threading

LISTEN = ("127.0.0.1", 19530)
TARGET = ("host.docker.internal", 29530)


def _pipe(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            destination.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, destination):
            try:
                sock.close()
            except OSError:
                pass


def _handle(client: socket.socket) -> None:
    try:
        remote = socket.create_connection(TARGET, 5)
    except OSError:
        client.close()
        return
    threading.Thread(target=_pipe, args=(client, remote), daemon=True).start()
    _pipe(remote, client)


def main() -> None:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(LISTEN)
    server.listen(16)
    while True:
        client, _addr = server.accept()
        threading.Thread(target=_handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
