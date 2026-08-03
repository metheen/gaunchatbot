"""ratelimit.RateLimiter birim testleri (deterministik — saat enjekte edilir)."""

from ratelimit import RateLimiter


def test_limit_asilinca_reddeder():
    rl = RateLimiter(max_hits=3, window_sec=60)
    # aynı anda (t=0) 3 istek serbest, 4.'sü reddedilir
    assert rl.allow("ip1", now=0) is True
    assert rl.allow("ip1", now=0) is True
    assert rl.allow("ip1", now=0) is True
    assert rl.allow("ip1", now=0) is False


def test_pencere_kayinca_yeniden_serbest():
    rl = RateLimiter(max_hits=2, window_sec=60)
    assert rl.allow("ip1", now=0) is True
    assert rl.allow("ip1", now=10) is True
    assert rl.allow("ip1", now=20) is False       # pencere içinde dolu
    assert rl.allow("ip1", now=61) is True         # ilk istek pencereden çıktı


def test_anahtarlar_bagimsiz():
    rl = RateLimiter(max_hits=1, window_sec=60)
    assert rl.allow("ip1", now=0) is True
    assert rl.allow("ip2", now=0) is True          # farklı IP etkilenmez
    assert rl.allow("ip1", now=0) is False


def test_gc_eski_anahtarlari_temizler():
    rl = RateLimiter(max_hits=5, window_sec=10, gc_threshold=2)
    rl.allow("a", now=0)
    rl.allow("b", now=0)
    # gc_threshold aşıldı; çok sonraki bir istek eski a/b'yi temizlemeli
    rl.allow("c", now=100)
    assert "a" not in rl._hits and "b" not in rl._hits
