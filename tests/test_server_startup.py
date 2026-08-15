import socket
import unittest

import server


class ServerStartupTests(unittest.TestCase):
    def test_port_is_listening_detects_bound_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        try:
            host, port = listener.getsockname()
            self.assertTrue(server.port_is_listening(host, port))
        finally:
            listener.close()

    def test_port_is_listening_rejects_closed_port(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        host, port = probe.getsockname()
        probe.close()
        self.assertFalse(server.port_is_listening(host, port))


if __name__ == '__main__':
    unittest.main()
