import socket, struct

__author__ = 'TheArchitect'

def ip2num(ip):
    ip = ip.split().reverse()
    ip = '.'.join(ip)
    return struct.unpack('L', socket.inet_aton(ip))[0]


def ip2bin(ip):
    ret = ''
    ip = ip.split('.')
    #ip.reverse()
    for oct in ip:
        ret += chr(int(oct))
    return ret


def bin2ip(bin):
    ip = ".".join([str(ord(i)) for i in bin])
    return ip


def num2ip(num):
    temp = socket.inet_ntoa(struct.pack('L', num)).split('.')
    temp.reverse()
    return '.'.join(temp)

