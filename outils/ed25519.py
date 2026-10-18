"""Signature Ed25519 (RFC 8032) en Python pur, sans module externe.

Lent (quelques dizaines de millisecondes) mais suffisant pour signer et vérifier une version du Facturier.
Le même code est recopié dans facturier.pyw pour la vérification.
"""
import hashlib

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x):
    return pow(x, _P - 2, _P)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    if x % 2:
        x = _P - x
    return x


_BY = 4 * _inv(5) % _P
_BX = _xrecover(_BY)
_B = (_BX, _BY, 1, _BX * _BY % _P)


def _add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _encode(pt):
    x, y, z, _ = pt
    zi = _inv(z)
    x, y = x * zi % _P, y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode(s):
    if len(s) != 32:
        raise ValueError("point invalide")
    y = int.from_bytes(s, "little")
    signe = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        raise ValueError("point invalide")
    x = _xrecover(y)
    if (x & 1) != signe:
        x = _P - x
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _P:
        raise ValueError("point hors de la courbe")
    return (x, y, 1, x * y % _P)


def _egal(p1, p2):
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _h(m):
    return hashlib.sha512(m).digest()


def _secret(cle):
    h = _h(cle)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def cle_publique(cle_privee: bytes) -> bytes:
    a, _ = _secret(cle_privee)
    return _encode(_mul(a, _B))


def signer(cle_privee: bytes, message: bytes) -> bytes:
    a, prefixe = _secret(cle_privee)
    pub = _encode(_mul(a, _B))
    r = int.from_bytes(_h(prefixe + message), "little") % _L
    rr = _encode(_mul(r, _B))
    k = int.from_bytes(_h(rr + pub + message), "little") % _L
    return rr + int.to_bytes((r + k * a) % _L, 32, "little")


def verifier(cle_pub: bytes, message: bytes, signature: bytes) -> bool:
    try:
        if len(signature) != 64:
            return False
        a = _decode(cle_pub)
        r = _decode(signature[:32])
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        k = int.from_bytes(_h(signature[:32] + cle_pub + message), "little") % _L
        return _egal(_mul(s, _B), _add(r, _mul(k, a)))
    except (ValueError, TypeError):
        return False
