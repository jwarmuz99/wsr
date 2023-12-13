import threading
import time
import log
import select

# import socket
import queue
import manager
import connection
import selectors
import socket

READ_ONLY = selectors.EVENT_READ

READ_WRITE = READ_ONLY | selectors.EVENT_WRITE
TIMEOUT = 80  # 80 seconds
POOL_ITERATIONS_TIMEOUT = 600  # 60 seconds


class ProxyDB(object):
    def __init__(self):
        self.db = {}  # proxy_object_id -> [proxy,thread]
        self.shutdown = False
        self.log = log.Log("proxy")

    def add_proxy(self, proxy, thread):
        self.db[id(proxy)] = [proxy, thread]

    def del_proxy(self, proxy):
        if proxy and not proxy.shutdown:
            proxy.close()
        if id(proxy) in self.db:
            del self.db[id(proxy)]

    def list(self):
        l = []
        for p in self.db.keys():
            l.append(self.db[p][0])
        return l

    def cleaner(self):
        while not self.shutdown:
            to_remove = []
            for p in self.db.keys():
                # if proxy is already mark as shutdown
                if self.db[p][0].shutdown:
                    to_remove.append(p)
                # else if thread is dead
                elif not self.db[p][1].is_alive():
                    try:
                        self.db[p][1]._Thread__stop()
                    except:
                        self.log.error("cannot stop thread!")
                    to_remove.append(p)
            for p in to_remove:
                self.log.debug("removing proxy %s" % p)
                try:
                    del self.db[p]
                except:
                    self.log.debug("diccionary has changed, cannot remove %s" % p)
            time.sleep(5)


class Proxy(object):
    def __init__(self, pool, sharestats=None, identifier=None):
        self.pool = pool
        self.miners_queue = {}
        self.pool_queue = queue.Queue()
        self.pool_queue.put("")
        self.pool.setblocking(0)
        if not identifier:
            identifier = str(id(self.miners_queue))[10:]
        self.id = identifier
        self.log = log.Log("pxy" + self.id)
        self.new_conns = []
        self.shares = sharestats
        self.manager = manager.Manager(
            sharestats=self.shares, identifier="mng" + self.id
        )
        self.shutdown = False
        self.selector = None

    def handle_socket_closure(self, s, iterations_to_die):
        if s is self.pool and iterations_to_die < 0:
            self.log.error("connection with pool lost!")
            self.miners_broadcast(self.manager.get_reconnect())
            iterations_to_die = 10
        else:
            self.log.error("connection with worker lost!")
            self.cleanup_socket(s)

    def cleanup_socket(self, s):
        try:
            self.log.info("closing socket")
            self.selector.unregister(s)
        except KeyError:
            self.log.error("socket was not registered, wtf?")
        fd = s.fileno()
        if fd in self.fd_to_socket:
            del self.fd_to_socket[fd]
        if fd in self.miners_queue:
            del self.miners_queue[fd]
        try:
            s.shutdown(socket.SHUT_RDWR)
            s.close()
        except OSError:
            self.log.error("Error closing socket")

    def handle_socket_write(self, s):
        # Verify if socket is valid before writing
        if s.fileno() != -1:
            if s is self.pool:
                if not self.pool_queue.empty():
                    msg = self.pool_queue.get()
                    self.log.debug("sending msg to pool: %s" % msg)
                    s.sendall(msg.encode())
            else:
                fd = s.fileno()
                if not self.miners_queue[fd].empty():
                    msg = self.miners_queue[fd].get()
                    self.log.debug("sending msg to miner: %s" % msg)
                    s.sendall(msg.encode())

    def check_pool_response(self, pool_ack, pool_ack_counter, iterations_to_die):
        if pool_ack:
            pool_ack_counter = POOL_ITERATIONS_TIMEOUT
        else:
            pool_ack_counter -= 1
            if pool_ack_counter < 1:
                self.log.error("pool is not responding, closing connections")
                self.miners_broadcast(self.manager.get_reconnect())
                if iterations_to_die < 0:
                    iterations_to_die = 10
                pool_ack_counter = POOL_ITERATIONS_TIMEOUT

    def set_auth(self, user, passw):
        if self.manager.authorized:
            self.log.info("sending new authorization to pool %s/%s" % (user, passw))
            self.pool_queue.put(self.manager.get_authorize(user, passw))
            time.sleep(1)
        else:
            self.log.info("setting initial pool authorization to %s/%s" % (user, passw))
        self.manager.username = user
        self.manager.password = passw

    def get_info(self):
        try:
            pool = str(self.pool.getpeername()[0])
            if pool in connection.dns:
                pool = connection.dns[pool]
            info = {"pool": pool}
            info["miners"] = []
            for s in self.fd_to_socket.keys():
                sock = self.fd_to_socket[s]
                if sock is not self.pool:
                    info["miners"].append(sock.getpeername()[0])
        except:
            self.log.error("some error while fetching proxy information")
            info = {}
        return info

    def add_miner(self, connection):
        if connection:
            self.miners_queue[connection.fileno()] = connection
            self.new_conns.append(connection)
            self.pool_queue.put(connection.recv(1024).decode())
            connection.setblocking(0)

    def miners_broadcast(self, msg):
        for q in self.miners_queue.keys():
            self.miners_queue[q].put(msg)

    def close(self):
        self.log.warning("closing proxy")
        self.shutdown = True
        for s in self.fd_to_socket.keys():
            try:
                self.fd_to_socket[s].shutdown(0)
                self.fd_to_socket[s].close()
            except:
                pass

    import selectors

    def start(self):
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.pool, selectors.EVENT_READ | selectors.EVENT_WRITE)
        self.fd_to_socket = {self.pool.fileno(): self.pool}
        iterations_to_die = -1
        pool_ack_counter = POOL_ITERATIONS_TIMEOUT

        while not self.shutdown:
            if iterations_to_die > 0:
                iterations_to_die -= 1

            if self.manager.force_exit or iterations_to_die == 0:
                self.close()
                return False

            if len(self.new_conns) > 0:
                conn = self.new_conns.pop(0)
                self.fd_to_socket[conn.fileno()] = conn
                self.selector.register(
                    conn, selectors.EVENT_READ | selectors.EVENT_WRITE
                )
                self.miners_queue[conn.fileno()] = queue.Queue()

            pool_ack = False
            events = self.selector.select(timeout=TIMEOUT)
            for key, mask in events:
                s = self.fd_to_socket[key.fd]

                # Check if the socket is valid
                if s.fileno() != -1:
                    # Handle read events
                    if mask & selectors.EVENT_READ:
                        data = s.recv(8196).decode()
                        if data:
                            if s is self.pool:
                                self.log.debug("got msg from pool: %s" % data)
                                self.miners_broadcast(
                                    self.manager.process(data, is_pool=True)
                                )
                                pool_ack = True
                            else:
                                self.log.debug("got msg from miner: %s" % data)
                                self.pool_queue.put(self.manager.process(data))
                        else:
                            self.handle_socket_closure(s, iterations_to_die)

                    # Handle write events
                    if mask & selectors.EVENT_WRITE:
                        self.handle_socket_write(s)

            self.check_pool_response(pool_ack, pool_ack_counter, iterations_to_die)
            time.sleep(0.1)

        self.selector.close()
