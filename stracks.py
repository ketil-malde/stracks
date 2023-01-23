# Main program

# Usage:
#  -C, --consensus
#    Output consensus annotation per image
#  -S, --stereo
#    Match detections in stereo pairs
#  -T, --track
#    Output tracks from video frames/sequential stills

import argparse

def bool_flag(s):
    """Parse boolean arguments from the command line."""
    if s.lower() in {"off", "false", "0"}:
        return False
    elif s.lower() in {"on", "true", "1"}:
        return True
    else:
        raise argparse.ArgumentTypeError("invalid value for a boolean flag")

desc = """Track detected objects, optionally linking stereo images and/or
          merging independent detections into a consensus"""
def make_args_parser():
    parser = argparse.ArgumentParser(prog='stracks', description=desc, add_help=True) # false?

    # Modes of operation
    parser.add_argument('--consensus', '-c', action='store_const', default=False, const=True,
        help="""Output consensus annotation per image.""")
    parser.add_argument('--stereo', '-s', action='store_const', default=False, const=True,
        help="""Process stereo images.""")

    # Tracking
    parser.add_argument('--track', default='True', type=bool_flag,
        help="""Generate tracks from video frames or seuqential stills.""")
    parser.add_argument('--max_age', '-m', default=None, type=int,
                        help="""Maximum age to search for old tracks to resurrect.""")
    parser.add_argument('--time_pattern', '-t', default='{}', type=str,
                        help="""Pattern to extract time from frame ID.""")
    parser.add_argument('--output', default="stracks.out", type=str, help="""Output file or directory""")

    parser.add_argument('FILES', metavar='FILES', type=str, nargs='*',
                        help='Files or directories to process')
    return parser

from tracking import bbmatch, bbdist_stereo
from definitions import BBox, Frame
import sys

# what if one frame is missing?
def zip_frames(lists):
    """Merge lists of frames, assumed to be named in lexically increasing order"""
    cur = ''
    results = []
    while not all([t == [] for t in lists]):
        heads = [l[0] for l in lists if l != []]
        tails = [l[1:] if l != [] else [] for l in lists]
        myframe = min([h.frameid for h in heads])
        assert cur < myframe, 'Error: frames not in lecially increasing order'
        cur = myframe
        res = []
        for i in range(len(lists)):
            if heads[i].frameid == myframe:
                res.append(heads[i])
            else:
                res.append(Frame(frameid=myframe,bboxes=[]))
                tails[i].insert(0,heads[i])
        results.append(res)
        lists = tails
    return results

def simple_consensus(framelist, frameindex=None): # :: [Frame] -> (Frame, extra data)
    """Build a consensus annotation for a set of frames"""
    # merge input lists (one frame from each?)
    def consensus(bbpair,i):  # todo: use tracking.consensus instead?
        """Merge two bboxes"""
        bb1, bb2 = bbpair
        if bb1 is None:  return bb2
        if bb2 is None:  return bb1
        fid = bb1.frameid
        a = i/(i+1)
        b = 1/(i+1)
        x = a*bb1.x + b*bb2.x
        y = a*bb1.y + b*bb2.y
        w = a*bb1.w + b*bb2.w
        h = a*bb1.h + b*bb2.h

        # this is probably not technically correct?
        if bb1.cls == bb2.cls:
            cl = bb1.cls
            p = bb1.pr + bb2.pr
        elif bb1.pr > bb2.pr:
            cl = bb1.cls
            p = bb1.pr - bb2.pr
        else:
            cl = bb2.cls
            p = bb2.pr - bb1.pr
            
        return BBox(fid,x,y,w,h,cl,p)
    
    def cons1(tup): # (Frame a,..) -> Frame a
        """Build consensus for a tuple of frames"""
        myframe=tup[0].frameid
        mybboxes=tup[0].bboxes
        i = 0
        for t in tup[1:]:
            if t.frameid != myframe:
                print(f'Error: frameID mismatch ("{t.frameid}" vs "{myframe}")')
                sys.exit(-1)
            else:
                i = i+1  # todo: whops, only if not None
                mybboxes = [consensus(pair, i) for pair in bbmatch(mybboxes, t.bboxes)]
                if False: # debugging
                    for t in tup:
                        print('***',t)
                    print(mybboxes,'\n')
        # todo: adjust probs into something meaningful (divide by len tup?)
        return Frame(frameid=myframe, bboxes=mybboxes)
 
    # - or separate index
    res = []
    for t in zip_frames(framelist):
        res.append(cons1(t))
    # return merged frames
    return res

def stereo(framelist): # :: Frame x Frame -> Frame of BBpairs

    def merge_frames(fs):
        (f1,f2) = fs
        assert f1.frameid == f2.frameid, f"Error: frameids don't match: {f1.frameid} vs {f2.frameid}"
        bbpairs = bbmatch(f1.bboxes, f2.bboxes, metric=bbdist_stereo)
        return Frame(frameid = f1.frameid, bboxes = bbpairs)

    # driver code is exactly as above - todo: refactor?
    [fr_left, fr_right] = framelist
    res = []
    for t in zip_frames([fr_left, fr_right]):
        res.append(merge_frames(t))
    return res

from tracking import tmatch

def track(frames):
    """Track single cam frames."""
    tracks = []
    old_tracks = []
    for f in frames:
        print(f.frameid)
        tmatch(f.bboxes, tracks, old_tracks, args.max_age, args.time_pattern) # match bboxes to tracks (tmatch)
    return tracks+old_tracks # sorted by time?

def process_tracks(tracks, interpolate=False, incrementpattern=None):
    # assumption: tracks sorted by first frameid
    # if incrementpatternm: interpolate by +1
    frames = []
    cur = []     # [[BBox]]
    tnum = 0
    tstats = {}
    for t in tracks:
        curframe = t.bbpairs[0].frameid

        # output all frames from cur until caught up
        def first(c): return c[0].frameid
        if cur != []:
            myfid = min([first(c) for c in cur])
            while myfid < curframe:
                # select out all myfids and build frame
                mybbs = [c[0] for c in cur if first(c) == myfid]
                frames.append(Frame(frameid=myfid, bboxes=mybbs))
                # purge myfids from cur
                c0 = [c[1:] for c in cur if first(c) == myfid]
                rest = [c for c in cur if first(c) != myfid]
                cur = [c for c in c0 + rest if c != []]
                if cur == []: break
                myfid = min([first(c) for c in cur])

        def setid(bbox, label): return BBox(frameid=bbox.frameid, x=bbox.x, y=bbox.y, w=bbox.w, h=bbox.h, cls=label, pr=bbox.pr)
        cur.insert(0,[setid(b,str(tnum)) for b in t.bbpairs]) # todo: make cls be tnum here!
        tstats[tnum] = {}
        for b in t.bbpairs: tstats[tnum][b.cls] = []
        for b in t.bbpairs: tstats[tnum][b.cls].append(b.pr)
        # how to summarize this?
        tnum += 1

    # process rest of cur (copy from above)
    while cur != []:
        myfid = min([first(c) for c in cur])
        mybbs = [c[0] for c in cur if first(c) == myfid]
        frames.append(Frame(frameid=myfid, bboxes=mybbs))
        # purge myfids from cur
        c0 = [c[1:] for c in cur if first(c) == myfid]
        rest = [c for c in cur if first(c) != myfid]
        cur = [c for c in c0 + rest if c != []]

    return frames, tstats

def strack(frames):
    """Track paired bboxes from a stereo camera"""
    pass

from parser import read_frames

if __name__ == '__main__':
    parser = make_args_parser()
    global args
    args = parser.parse_args()
    if args.consensus and args.stereo:
        print('Error: Unsupported combination of arguments:')
        print(args)
    elif args.stereo:
        if len(args.FILES) != 2:
            print(f'Error: Wrong number of files {len(args.FILES)} instead of 2.')
            sys.exit(-1)
        else:
            fs = [read_frames(f) for f in args.FILES]
            res1 = stereo(fs)
    elif args.consensus:
        fs = [read_frames(f) for f in args.FILES]
        res1 = simple_consensus(fs)
        # output and/or do tracking
    else:
        if len(args.FILES) > 1:
            print(f'Error: Too many files, consider -s or -c')
            sys.exit(-1)
        res1 = read_frames(args.FILES[0])

    if args.track:
        # todo: if pattern/enumeration is given, insert empty frames
        ts = track(res1)

        def firstframe(t): return t.bbpairs[0].frameid
        ts.sort(key=firstframe)

        # interpolate dummy detections in tracks
        # maybe split up if too long gaps?

        for x in ts:
            print('Track:')
            for b in x.bbpairs:
                print('   ',b)
        print()

        fs, ss = process_tracks(ts)
        for f in fs:
            for b in f.bboxes:
                print('  ',b)
        for s in ss:
            print('Track:', s, ss[s])

    else:
        # just output res1 (::[Frame])
        for x in res1:
            print(x)

