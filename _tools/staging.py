#!/usr/bin/env python3
"""智遇未来官网 · 测试区工具（生产 phyture.ai/  <->  测试 phyture.ai/test/）

根目录 = 生产；test/ = 生产的完整镜像。镜像时把本站绝对地址 https://phyture.ai/ 改写成
https://phyture.ai/test/（og:url、og:image 等，否则测试页的微信卡片会去取生产的图），并给页面加测试版标记
（橙色边框、底部「测试版 · 请勿外发」、标题前【测试】）；promote 时两样都原样去掉。

  python3 _tools/staging.py status               test 与生产差在哪、git 状态、下一步做什么
  python3 _tools/staging.py sync [--force]       把生产的新改动带进 test/（保留 test 里没上线的改动，两边都改过就尝试合并；
                                                 --force 整个按生产重建）
  python3 _tools/staging.py adopt                根目录被直接改了：把改动挪进 test/，根目录恢复成已上线版本
  python3 _tools/staging.py mark                 给 test/ 里缺标记的页面补上测试版标记
  python3 _tools/staging.py check [--stage test|prod] [--bootstrap]
                                                 查链接、本站绝对地址、分享卡片（og:*）；--stage 再核对这次要提交的范围
  python3 _tools/staging.py promote [--yes] [--delete] [--override]
                                                 把 test/ 里已推送、已线上验证的改动写回根目录（不加 --yes 只列清单；
                                                 --delete 同步删除，--override 冲突时以 test 为准）

只用标准库。git 只读（--no-optional-locks），add / commit / push 一律由人在自己电脑上做。
"""
import argparse, os, struct, subprocess, sys, tempfile
from html.parser import HTMLParser
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = 'test'
PROD_URL, TEST_URL = 'https://phyture.ai/', 'https://phyture.ai/test/'
TEXT_EXT = {'.html', '.htm', '.css', '.js', '.mjs', '.json', '.xml', '.txt', '.svg', '.webmanifest', '.md'}
ROOT_ONLY = {'CNAME', 'robots.txt', '.nojekyll', '.gitignore', 'README.md'}   # 只属于根目录，不进镜像
BOOTSTRAP_OK = {'robots.txt', '.gitignore'}                                  # 仅首次搭建时允许随测试区一起提交
IGNORE_NAMES = {'.DS_Store'}
KIND = {'changed': '改动', 'added': '新增', 'removed': '删除'}
SIDE_NOTE = {'test': '', 'prod': '   <- 生产在 test 之后被直接改过（先 sync）',
             'both': '   <- test 和生产都改过（冲突：先 sync 尝试合并）'}


# ------------------------------------------------------------------ 文件与改写
def is_text(rel):
    return os.path.splitext(rel)[1].lower() in TEXT_EXT

def is_html(rel):
    return rel.lower().endswith(('.html', '.htm'))

# 测试版标记：只加在 test/ 的页面上（橙色边框 + 底部「测试版 · 请勿外发」+ 标题前【测试】），promote 时原样去掉
TAG = '【测试】'.encode()
TITLE_SPOTS = [b'<title>', b'<meta property="og:title" content="', b'<meta itemprop="name" content="']
MARK_BEGIN, MARK_END = b'<!--staging:test-->', b'<!--/staging:test-->'
MARK = (MARK_BEGIN + '<style>'
        '.__stg-frame{position:fixed;top:0;right:0;bottom:0;left:0;border:4px solid #FF6A00;pointer-events:none;z-index:2147483646}'
        '.__stg-pill{position:fixed;left:50%;bottom:calc(14px + env(safe-area-inset-bottom));transform:translateX(-50%);'
        'z-index:2147483647;pointer-events:none;background:#FF6A00;color:#fff;border-radius:999px;padding:9px 16px;'
        'font:700 13px/1 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;letter-spacing:.08em;'
        'white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.18)}'
        '</style><div class="__stg-frame" aria-hidden="true"></div>'
        '<div class="__stg-pill" aria-hidden="true">测试版 · 请勿外发</div>'.encode() + MARK_END + b'\n')

def add_mark(b):
    for spot in TITLE_SPOTS:                      # 每种位置只动第一处
        i = b.find(spot)
        if i >= 0:
            j = i + len(spot)
            b = b[:j] + TAG + b[j:]
    i = b.rfind(b'</body>')
    return b[:i] + MARK + b[i:] if i >= 0 else b

def strip_mark(b):
    for spot in TITLE_SPOTS:
        i = b.find(spot)
        if i >= 0:
            j = i + len(spot)
            if b[j:j + len(TAG)] == TAG:
                b = b[:j] + b[j + len(TAG):]
    i = b.find(MARK_BEGIN)
    if i >= 0:
        k = b.find(MARK_END, i)
        if k >= 0:
            k += len(MARK_END)
            if b[k:k + 1] == b'\n':
                k += 1
            b = b[:i] + b[k:]
    return b

def has_mark(b):
    """(有标记块, 标题带【测试】)"""
    i = b.find(b'<title>')
    return MARK_BEGIN in b, i >= 0 and b[i + 7:i + 7 + len(TAG)] == TAG

def fwd(rel, b):
    """生产 → 测试：本站绝对地址改成 /test/，页面加测试版标记"""
    if b is None or not is_text(rel):
        return b
    b = b.replace(PROD_URL.encode(), TEST_URL.encode())
    return add_mark(b) if is_html(rel) else b

def inv(rel, b):
    """测试 → 生产：去掉测试版标记，地址改回生产"""
    if b is None or not is_text(rel):
        return b
    if is_html(rel):
        b = strip_mark(b)
    return b.replace(TEST_URL.encode(), PROD_URL.encode())

def read(p):
    with open(p, 'rb') as f:
        return f.read()

def read_opt(p):
    return read(p) if os.path.isfile(p) else None

def write(p, b):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(b)

def remove(p):
    try:
        os.remove(p)
        return True
    except OSError:
        return False

def in_scope(rel):
    """根目录下的这个路径是否属于网站（会被镜像进 test/）"""
    parts = rel.split('/')
    return parts[0] != TEST and rel not in ROOT_ONLY and not any(x.startswith(('.', '_')) for x in parts)

def list_files(sub):
    """sub='' 生产 / sub='test' 测试 → {相对路径: 绝对路径}。跳过点文件、下划线目录（GitHub Pages 不发布）"""
    base = os.path.join(ROOT, sub) if sub else ROOT
    out = {}
    if not os.path.isdir(base):
        return out
    for d, dirs, files in os.walk(base):
        rd = os.path.relpath(d, base).replace(os.sep, '/')
        rd = '' if rd == '.' else rd + '/'
        dirs[:] = sorted(x for x in dirs if not x.startswith(('.', '_')) and (sub or rd + x != TEST))
        for f in sorted(files):
            r = rd + f
            if not f.startswith(('.', '_')) and r not in ROOT_ONLY:
                out[r] = os.path.join(d, f)
    return out

def brief(items, n=3):
    items = list(items)
    return '、'.join(items[:n]) + (f' 等 {len(items)} 个' if len(items) > n else '')


# ------------------------------------------------------------------ git（只读）
def _git(args, text=True):
    try:
        return subprocess.run(['git', '--no-optional-locks', '-c', 'core.quotePath=false'] + list(args),
                              cwd=ROOT, capture_output=True, text=text, check=True).stdout
    except Exception:
        return None

def dirty_paths():
    """未提交 / 未跟踪的路径（相对仓库根）；拿不到 git 时返回 None"""
    out = _git(['status', '--porcelain', '-z', '--untracked-files=all'])
    if out is None:
        return None
    items, parts, i = [], out.split('\0'), 0
    while i < len(parts):
        e, i = parts[i], i + 1
        if len(e) < 4:
            continue
        items.append(e[3:])
        if e[0] in 'RC' and i < len(parts):   # 重命名 / 复制：下一段是原路径
            items.append(parts[i])
            i += 1
    return [p for p in items if os.path.basename(p) not in IGNORE_NAMES]

def ahead_behind():
    out = _git(['rev-list', '--left-right', '--count', 'HEAD...@{u}'])
    try:
        a, b = out.split()
        return int(a), int(b)
    except Exception:
        return None

def root_dirty(dirty):
    return [p for p in (dirty or []) if in_scope(p)]


# ------------------------------------------------------------------ test 与生产的差异
def compare():
    """[(相对路径, changed / added / removed)]；added = 只在 test，removed = 只在生产"""
    prod, test = list_files(''), list_files(TEST)
    diffs = []
    for r, p in test.items():
        b = read(p)
        if r not in prod:
            diffs.append((r, 'added'))
        elif read(prod[r]) != inv(r, b):
            diffs.append((r, 'changed'))
    diffs += [(r, 'removed') for r in prod if r not in test]
    return sorted(diffs)

def base_of(r):
    """基准 = git 历史里 test 与生产最近一次一致时的生产版本（找不到就用 test 文件刚建时的生产版本）。
    返回 (test 文件是否提交过, 基准内容)"""
    tp = TEST + '/' + r
    seen, base = False, None
    for k in (_git(['log', '--format=%H', '--', r, tp]) or '').split():
        tk = _git(['show', f'{k}:{tp}'], text=False)
        if tk is None:
            continue
        pk = _git(['show', f'{k}:{r}'], text=False)
        seen, base = True, pk
        if inv(r, tk) == pk:                       # 在生产形态下比（与测试版标记的写法无关）
            break
    return seen, base

def merge3(base, ours, theirs):
    """三方合并（git merge-file）：干净合并返回结果，冲突或出错返回 None"""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, b in (('ours', ours), ('base', base), ('theirs', theirs)):
            paths.append(os.path.join(d, name))
            write(paths[-1], b or b'')
        try:
            out = subprocess.run(['git', 'merge-file', '-p'] + paths, capture_output=True)
        except Exception:
            return None
        return out.stdout if out.returncode == 0 else None

def side(r):
    """一处差异是哪边改的：'test' = test 里还没上线的改动；'prod' = 生产被直接改过、test 过期；
    'both' = 两边都改过、test 里还没包含生产那边的改动（冲突）"""
    t_cur = read_opt(os.path.join(ROOT, TEST, r))
    p_cur = read_opt(os.path.join(ROOT, r))
    seen, base = base_of(r)
    if not seen:                                   # test 里这个文件还没提交过
        return 'test' if t_cur is not None else 'prod'
    t_edit, p_edit = inv(r, t_cur) != base, p_cur != base
    if t_edit and p_edit:
        if is_text(r) and t_cur is not None and p_cur is not None:
            ours = inv(r, t_cur)
            if merge3(base, ours, p_cur) == ours:  # 生产那边的改动已经在 test 里了（人工合并过）
                return 'test'
        return 'both'
    return 'prod' if p_edit else 'test'

def classify():
    return [(r, k, side(r)) for r, k in compare()]

def show(diffs):
    for r, k, s in diffs:
        print(f'  {KIND[k]}  {r}{SIDE_NOTE[s]}')


# ------------------------------------------------------------------ 检查
class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs, self.meta, self.title, self._t = [], {}, '', False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ('href', 'src', 'poster'):
            if a.get(k):
                self.refs.append((a[k].strip(), self.getpos()[0]))
        if tag == 'meta' and a.get('content') is not None:
            key = a.get('property') or a.get('name') or a.get('itemprop')
            if key:
                self.meta.setdefault(key, a['content'])
        self._t = self._t or tag == 'title'

    def handle_endtag(self, tag):
        if tag == 'title':
            self._t = False

    def handle_data(self, data):
        if self._t:
            self.title += data

def img_size(p):
    b = read(p)
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return struct.unpack('>II', b[16:24])
    if b[:6] in (b'GIF87a', b'GIF89a'):
        return struct.unpack('<HH', b[6:10])
    if b[:2] == b'\xff\xd8':
        i = 2
        while i + 9 < len(b):
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack('>HH', b[i + 5:i + 9])
                return w, h
            if m == 0xFF:
                i += 1
            elif m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
            else:
                i += 2 + struct.unpack('>H', b[i + 2:i + 4])[0]
    return None

def check_scope(sub, errs, warns, infos, focus=()):
    """sub='' 查生产，sub='test' 查测试；focus = 这次改动的页面（只给它们列分享卡片）"""
    files = list_files(sub)
    base = os.path.join(ROOT, sub) if sub else ROOT
    tag = sub + '/' if sub else ''
    own = TEST_URL if sub else PROD_URL
    for r, p in files.items():
        if not is_text(r):
            continue
        raw = read(p)
        s = raw.decode('utf-8', 'replace')
        if is_html(r):                                          # 测试版标记
            blk, ttl = has_mark(raw)
            if sub and fwd(r, inv(r, raw)) != raw:
                warns.append(f'{tag}{r}  测试版标记缺失或位置不对（运行 python3 _tools/staging.py mark）')
            if not sub and (blk or any(spot + TAG in raw for spot in TITLE_SPOTS)):
                errs.append(f'{tag}{r}  生产页面里带着测试版标记（{TAG.decode()} 或 staging:test）')
        for n, line in enumerate(s.splitlines(), 1):          # 本站绝对地址
            if sub and line.replace(TEST_URL, '').count(PROD_URL):
                errs.append(f'{tag}{r}:{n}  指向生产的绝对地址（test 里应写 {TEST_URL}…）')
            if not sub and TEST_URL in line:
                errs.append(f'{tag}{r}:{n}  生产文件里出现了测试地址 {TEST_URL}')
            for odd in ('http://phyture.ai', 'www.phyture.ai', 'https://phyture.ai"', "https://phyture.ai'"):
                if odd in line:
                    warns.append(f'{tag}{r}:{n}  出现 {odd}（不会被自动改写，请确认）')
        if not r.endswith(('.html', '.htm')):
            continue
        pg = Page()
        pg.feed(s)
        d = os.path.dirname(r)
        for v, n in pg.refs:                                    # 站内链接与资源
            if v.lower().startswith(('http:', 'https:', 'mailto:', 'tel:', 'data:', 'javascript:', '#', '//', 'weixin:')):
                continue
            path = unquote(v.split('#', 1)[0].split('?', 1)[0])
            if not path:
                continue
            if path.startswith('/'):
                (errs if sub else warns).append(f'{tag}{r}:{n}  以 / 开头的地址 {v}（在 test 里会指到生产，改成相对地址）')
                continue
            tgt = os.path.normpath(os.path.join(d, path)).replace(os.sep, '/')
            if tgt.startswith('..'):
                errs.append(f'{tag}{r}:{n}  {v} 跳出了{"test/" if sub else "网站根目录"}')
                continue
            if tgt == '.':
                tgt = 'index.html'
            elif path.endswith('/') or os.path.isdir(os.path.join(base, tgt)):
                tgt += '/index.html'
            if tgt not in files:
                errs.append(f'{tag}{r}:{n}  链接或资源不存在：{v}')
        m, img, size = pg.meta, pg.meta.get('og:image'), ''  # 分享卡片
        if not (img or m.get('og:title')):
            continue
        if img and not img.startswith('https://'):
            errs.append(f'{tag}{r}  og:image 必须是 https 绝对地址：{img}')
        elif img and img.startswith(own):
            rel = img[len(own):].split('?', 1)[0]
            if rel not in files:
                errs.append(f'{tag}{r}  og:image 指向的文件不存在：{img}')
            else:
                wh, kb = img_size(files[rel]), os.path.getsize(files[rel]) // 1024
                size = f'{wh[0]}×{wh[1]} {kb}KB' if wh else f'{kb}KB'
                if wh and min(wh) < 300:
                    warns.append(f'{tag}{r}  og:image 小于 300×300，微信可能不用')
                if kb > 300:
                    warns.append(f'{tag}{r}  og:image {kb}KB 偏大（建议 < 300KB）')
                ow, oh = m.get('og:image:width', '').strip(), m.get('og:image:height', '').strip()
                if wh and ow and oh and (ow, oh) != (str(wh[0]), str(wh[1])):
                    warns.append(f'{tag}{r}  og:image:width/height 写 {ow}×{oh}，实际 {wh[0]}×{wh[1]}')
                if wh and wh[0] != wh[1] and r in focus:
                    infos.append(f'{tag}{r}  og:image 不是方图（{wh[0]}×{wh[1]}），微信卡片会裁中间的正方形')
        if r in focus:
            title = (m.get('og:title') or pg.title).strip()
            pic = img.rsplit('/', 1)[-1] if img else '（无图）'
            infos.append(f'{tag}{r}  卡片：「{title}」｜{m.get("og:description", "（无摘要）")}｜{pic} {size}'.rstrip())


# ------------------------------------------------------------------ 命令
def cmd_status(a):
    dirty = dirty_paths()
    if not list_files(TEST):
        print('还没有 test/。首次搭建见 skill「首次搭建」')
        return 0
    diffs = classify()
    print('test/ 与生产：' + ('一致' if not diffs else f'{len(diffs)} 处不同'))
    show(diffs)
    rd = root_dirty(dirty)
    td = [p for p in (dirty or []) if p.startswith(TEST + '/')]
    other = [p for p in (dirty or []) if p not in rd and p not in td]
    ab = ahead_behind()
    if dirty is None:
        print('git：读不到')
    else:
        print(f'git：test/ 未提交 {len(td)} 个｜根目录（生产）未提交 {len(rd)} 个｜其他 {len(other)} 个'
              + (f'｜本地领先 origin {ab[0]}、落后 {ab[1]} 个提交' if ab else ''))
        for p in rd:
            print('  根目录未提交：', p)
        if other:
            print('  其他未提交：', brief(other))
    sides = {s for _, _, s in diffs}
    unmarked = [r for r, p in list_files(TEST).items() if is_html(r) and fwd(r, inv(r, read(p))) != read(p)]
    if ab and ab[1]:
        step = '先在自己电脑上 git pull，拿到最新的线上版本'
    elif rd and not diffs:
        step = '已 promote、还没提交：check --stage prod 通过后提交并推送（上线）'
    elif rd:
        step = '根目录被直接改了：python3 _tools/staging.py adopt（改动挪进 test/，根目录恢复）'
    elif unmarked:
        step = f'test/ 有 {len(unmarked)} 个页面的测试版标记缺失或位置不对：python3 _tools/staging.py mark'
    elif 'both' in sides:
        step = '有冲突（同一文件 test 和生产都改过）：先 sync 尝试自动合并；合并不了就人工把生产的改动补进 test/'
    elif 'prod' in sides:
        step = 'test 过期：python3 _tools/staging.py sync（只把生产的新改动带进 test）'
    elif td and not (_git(['ls-tree', '--name-only', 'HEAD', TEST]) or '').strip():
        step = ('首次搭建：check --stage test --bootstrap 通过后，提交 test/ _tools/ robots.txt .gitignore '
                '并推送（上测试区）')
    elif td:
        step = '改完了：check --stage test，然后提交 test/ 并推送（上测试区）'
    elif ab and ab[0]:
        step = '有提交还没推送：git push'
    elif diffs:
        step = '测试区已上线：线上检查 phyture.ai/test/…，确认无误后 promote'
    else:
        step = '全部一致。要改东西，直接改 test/ 里的文件'
    print('下一步：' + step)
    return 0

def put_test(r):
    """用生产版本覆盖 test/ 里的这个文件（生产没有就删掉）；返回是否成功"""
    src, dst = os.path.join(ROOT, r), os.path.join(ROOT, TEST, r)
    b = read_opt(src)
    if b is None:
        return remove(dst) or not os.path.exists(dst)
    write(dst, fwd(r, b))
    return True

def cmd_sync(a):
    dirty = dirty_paths()
    rd = root_dirty(dirty)
    if rd and not a.force:
        print('根目录有未提交的改动（根目录只能由 promote 改）。要保留就先 adopt：')
        for p in rd:
            print('  ', p)
        return 1
    fresh = not list_files(TEST)
    merged, keep = [], []
    if fresh or a.force:
        todo = [r for r, _ in compare()]
    else:
        diffs = classify()
        todo = [r for r, _, s in diffs if s == 'prod']
        for r, k, s in diffs:
            if s == 'both' and k == 'changed' and is_text(r):   # 两边都改过：能干净合并就合并进 test
                tp = os.path.join(ROOT, TEST, r)
                m = merge3(base_of(r)[1], inv(r, read(tp)), read(os.path.join(ROOT, r)))
                if m is not None:
                    write(tp, fwd(r, m))
                    merged.append(r)
                    continue
            if s != 'prod':
                keep.append((r, k, s))
    failed = [r for r in todo if not put_test(r)]
    print(f'test/ 已同步：从生产带进 {len(todo) - len(failed)} 个文件')
    for r in merged:
        print(f'  已把生产的改动合并进 test/{r}（test 自己的改动保留），推到测试区重新验证')
    for r in failed:
        print('  没删掉（没有删除权限？请手动删）：test/' + r)
    if keep:
        print('保留 test/ 里还没上线的改动：')
        show(keep)
    return 1 if failed or any(s == 'both' for _, _, s in keep) else 0

def cmd_adopt(a):
    dirty = dirty_paths()
    if dirty is None:
        print('读不到 git 状态，没法判断哪些是没上线的改动')
        return 1
    rd = root_dirty(dirty)
    if not rd:
        print('根目录没有未提交的改动，不需要 adopt')
        return 0
    had_test = bool(list_files(TEST))
    if had_test:
        busy = {r for r, _, s in classify() if s in ('test', 'both')}
        clash = [p for p in rd if p in busy]
        if clash:
            print('这些文件在 test/ 里也有没上线的改动，不能自动合并，请人工处理：')
            for p in clash:
                print('  ', p)
            return 1
    saved = {p: read_opt(os.path.join(ROOT, p)) for p in rd}
    failed = []
    for p in rd:                                   # 根目录恢复成 HEAD（已上线的版本）
        head = _git(['show', 'HEAD:' + p], text=False)
        if head is not None:
            write(os.path.join(ROOT, p), head)
        elif not remove(os.path.join(ROOT, p)):
            failed.append(p)
    if not had_test:                               # 第一次：先按（恢复后的）生产建镜像
        failed += [TEST + '/' + r for r, _ in compare() if not put_test(r)]
    for p, b in saved.items():                     # 改动写进 test/
        dst = os.path.join(ROOT, TEST, p)
        if b is not None:
            write(dst, fwd(p, b))
        elif os.path.exists(dst) and not remove(dst):
            failed.append(TEST + '/' + p)
    print(f'已把 {len(rd)} 个改动挪进 test/，根目录恢复成已上线版本：')
    for p in rd:
        print('  ', p, '->', TEST + '/' + p)
    for p in failed:
        print('  没删掉（没有删除权限？请手动删）：', p)
    return 1 if failed else 0

def cmd_mark(a):
    """把 test/ 里的页面规范成「生产版本 + 测试版标记」：补上缺的标记（改动内容不受影响）"""
    n = 0
    for r, p in list_files(TEST).items():
        if not is_html(r):
            continue
        b = read(p)
        want = fwd(r, inv(r, b))
        if want != b:
            write(p, want)
            n += 1
    print(f'已规范 test/ 里 {n} 个页面的测试版标记' if n else 'test/ 里的页面测试版标记都正常')
    return 0

def cmd_check(a):
    errs, warns, infos = [], [], []
    dirty = dirty_paths()
    focus = {r for r, _ in compare()} if list_files(TEST) else set()
    if not list_files(TEST):
        errs.append('还没有 test/：先搭建（见 skill）')
    else:
        check_scope(TEST, errs, warns, infos, focus)
    if a.stage == 'prod':
        check_scope('', errs, warns, infos, set(root_dirty(dirty)))
    if a.stage and dirty is None:
        warns.append('读不到 git 状态，无法核对提交范围')
    elif a.stage == 'test':
        bad = [p for p in dirty if not (p.startswith((TEST + '/', '_tools/')) or (a.bootstrap and p in BOOTSTRAP_OK))]
        for p in bad:
            hint = '首次搭建加 --bootstrap' if p in BOOTSTRAP_OK else '根目录只能由 promote 改'
            errs.append(f'这次不该提交的文件：{p}（测试阶段只改 test/；{hint}）')
        if not any(p.startswith(TEST + '/') for p in dirty):
            warns.append('test/ 没有未提交的改动，这次没有东西要推到测试区')
    elif a.stage == 'prod':
        for r, k in compare():
            errs.append(f'根目录与 test/ 不一致：{KIND[k]} {r}（先 promote --yes）')
        td = [p for p in dirty if p.startswith(TEST + '/')]
        if td:
            errs.append(f'test/ 有未提交的改动：{brief(td)}（生产只收推到线上测过的版本）')
        for p in dirty:
            if not p.startswith(TEST + '/') and not in_scope(p):
                warns.append(f'{p} 也有改动，会一起提交')
        if not root_dirty(dirty):
            warns.append('根目录没有未提交的改动，这次没有东西要上线')
    for s in infos:
        print('  ·', s)
    for s in warns:
        print('  !', s)
    for s in errs:
        print('  ✗', s)
    print('检查通过' if not errs else f'有 {len(errs)} 个问题，先修掉再推送', f'（{len(warns)} 个提醒）' if warns else '')
    return 1 if errs else 0

def cmd_promote(a):
    dirty = dirty_paths()
    diffs = classify()
    if not diffs:
        print('test/ 与生产一致，没有要上线的改动')
        return 0
    print('将要上线（test/ -> 根目录）：')
    show(diffs)
    problems = []
    if dirty is None:
        problems.append('读不到 git 状态，无法确认 test/ 已推送、线上验证过')
    else:
        td = [p for p in dirty if p.startswith(TEST + '/')]
        if td:
            problems.append(f'test/ 有未提交的改动（{brief(td)}）：生产只收推到线上测过的版本')
        rd = root_dirty(dirty)
        if rd:
            problems.append(f'根目录有未提交的改动（{brief(rd)}）：先 adopt 或处理掉')
    ab = ahead_behind()
    if ab and ab[0]:
        problems.append(f'本地还有 {ab[0]} 个提交没推送：test 的改动还没上线测过')
    if ab and ab[1]:
        problems.append(f'本地落后 origin {ab[1]} 个提交：先 git pull')
    for r, k, s in diffs:
        if s == 'prod':
            problems.append(f'{r}：生产在 test 之后被直接改过，先 sync、重新上测试区验证')
        elif s == 'both' and not a.override:
            problems.append(f'{r}：test 和生产都改过，先 sync 合并（或人工补进 test/）、重新上测试区验证；'
                            '确认就用 test 的版本覆盖生产，加 --override')
        elif s == 'both':
            print(f'  ! {r}：用 test 的版本覆盖生产那边的直接改动（--override）')
    errs = []
    check_scope(TEST, errs, [], [])
    problems += ['test/ 检查未通过：' + e for e in errs]
    if problems:
        print('不能上线：')
        for p in problems:
            print('  ✗', p)
        return 1
    if not a.yes:
        print('（只是清单。线上检查确认无误后：python3 _tools/staging.py promote --yes）')
        return 0
    done, left = [], []
    for r, k, s in diffs:
        if k == 'removed':
            (done if a.delete and remove(os.path.join(ROOT, r)) else left).append(r)
            continue
        b = read(os.path.join(ROOT, TEST, r))
        write(os.path.join(ROOT, r), inv(r, b))
        done.append(r)
    print(f'已写回根目录：{len(done)} 个')
    for r in left:
        print(f'  未删除 {r}（要从生产删掉：加 --delete，或手动 git rm）')
    q = lambda s: "'" + s.replace("'", "'\\''") + "'"
    if done:
        print('下一步：python3 _tools/staging.py check --stage prod 通过后，在自己电脑上执行：')
        print('  git add -A -- ' + ' '.join(q(r) for r in done) + ' && git commit -m "release: <这次改了什么>" && git push')
    return 0

def main():
    ap = argparse.ArgumentParser(description='智遇未来官网测试区工具（phyture.ai/test/）')
    sp = ap.add_subparsers(dest='cmd')
    sp.add_parser('status')
    sp.add_parser('adopt')
    sp.add_parser('mark')
    s = sp.add_parser('sync')
    s.add_argument('--force', action='store_true')
    c = sp.add_parser('check')
    c.add_argument('--stage', choices=['test', 'prod'])
    c.add_argument('--bootstrap', action='store_true')
    p = sp.add_parser('promote')
    p.add_argument('--yes', action='store_true')
    p.add_argument('--delete', action='store_true')
    p.add_argument('--override', action='store_true')
    a = ap.parse_args()
    fn = {'status': cmd_status, 'sync': cmd_sync, 'adopt': cmd_adopt, 'mark': cmd_mark,
          'check': cmd_check, 'promote': cmd_promote}.get(a.cmd)
    if not fn:
        ap.print_help()
        return 0
    return fn(a)

if __name__ == '__main__':
    sys.exit(main())
