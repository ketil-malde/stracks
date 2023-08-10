# Main program

# Usage:
#  -c, --consensus
#    Generate consensus annotation per image
#  -s, --stereo
#    Match detections in stereo pairs
#  -t, --track=True/False
#    Extract tracks from video frames/sequential stills

import argparse

from parse import parse

def intpair(s):
    """Parse a pair of integers from the command line"""
    w,h = parse("{:d},{:d}", s)
    if w is None or h is None:
        printf(f'Error: can\'t parse {s} as a pair of integers')
        exit(255)
    else:
        return((int(w), int(h)))

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
    parser.add_argument('--track', default='True', action=argparse.BooleanOptionalAction,
        help="""Generate tracks from video frames or seuqential stills.""")
    parser.add_argument('--max_age', '-m', default=None, type=int,
                        help="""Maximum age to search for old tracks to resurrect.""")
    parser.add_argument('--time_pattern', '-t', default='{}', type=str,
                        help="""Pattern to extract time from frame ID.""")
    parser.add_argument('--scale', default=1.0, type=float, help="""Size of the search space to link detections.""")
    parser.add_argument('--interpolate', default=False, action=argparse.BooleanOptionalAction, help="""Generate virtual detections by interpolating""")

    parser.add_argument('--shape', default=(1228,1027), type=intpair, help="""Image dimensions, width and height.""")    
    parser.add_argument('--output', '-o', default=None, type=str, help="""Output file or directory""")

    parser.add_argument('FILES', metavar='FILES', type=str, nargs='*',
                        help='Files or directories to process')
    return parser

from tracking import bbmatch, bbdist_stereo, bbdist_track
from definitions import BBox, Frame
import sys

# what if one frame is missing?
def zip_frames(lists):
    """Merge lists of frames, assumed to be named in lexically increasing order"""
    cur = ''
    results = []
    while not all([t == [] for t in lists]):
        heads = [l[0] if l != [] else None for l in lists ]
        tails = [l[1:] if l != [] else [] for l in lists]
        myframe = min([h.frameid for h in heads if h is not None])
        assert cur < myframe, 'Error: frames not in lecially increasing order'
        cur = myframe
        res = []

        for i in range(len(heads)):
            if heads[i] is None:
                res.append(Frame(frameid=myframe,bboxes=[]))
            elif heads[i].frameid == myframe:
                res.append(heads[i])
            else:
                res.append(Frame(frameid=myframe,bboxes=[]))
                tails[i].insert(0,heads[i])
        results.append(res)
        lists = tails
    return results

def consensus_frame(tup):
    """Build consensus for a tuple of frames"""

    def consensus(bbpair,i,n):
        """Merge two bboxes"""
        bb1, bb2 = bbpair
        
        a = i/(i+1) # weight_current (bb1)
        b = 1/(i+1) # weight_next (bb2)

        if bb1 is None:
            fid = bb2.frameid
            x,y,w,h,cl = bb2.x, bb2.y, bb2.w, bb2.h, bb2.cls
            p = bb2.pr*b
        elif bb2 is None:
            fid = bb1.frameid
            x,y,w,h,cl = bb1.x, bb1.y, bb1.w, bb1.h, bb1.cls
            p = bb1.pr*a
        else:
            fid = bb1.frameid            
            x = a*bb1.x + b*bb2.x
            y = a*bb1.y + b*bb2.y
            w = a*bb1.w + b*bb2.w
            h = a*bb1.h + b*bb2.h
            p = bb1.pr*a + bb2.pr*b
            cl = bb1.cls if bb1.pr*a > bb2.pr*b else bb2.cls

        return BBox(fid,x,y,w,h,cl,p)

    myframe=tup[0].frameid
    mybboxes=tup[0].bboxes
    num_classes = len(tup)
    i = 0
    for t in tup[1:]:
        if t.frameid != myframe:
            error(f'FrameID mismatch ("{t.frameid}" vs "{myframe}")')
        else:
            i = i+1  # todo: whops, only if not None
            mybboxes = [consensus(pair, i, num_classes) for pair in bbmatch(mybboxes, t.bboxes, metric=bbdist_track, scale=args.scale)]
            if False: # debugging
                for t in tup:
                    print('***',t)
                print(mybboxes,'\n')
                # todo: adjust probs into something meaningful (divide by len tup?)
    return Frame(frameid=myframe, bboxes=mybboxes)
 
def merge_frames(fs):
    (f1,f2) = fs
    assert f1.frameid == f2.frameid, f"Error: frameids don't match: {f1.frameid} vs {f2.frameid}"
    bbpairs = bbmatch(f1.bboxes, f2.bboxes, metric=bbdist_stereo, scale=1)
    return Frame(frameid = f1.frameid, bboxes = bbpairs)

from tracking import tmatch

def track(frames, metric):
    """Track single cam frames."""
    tracks = []
    old_tracks = []
    for f in frames:
        # print(f'FrameID {f.frameid} boxes {len(f.bboxes)}')
        # def boxes(ts): return [b for t in ts for b in t.bbpairs]
        tmatch(f.bboxes, tracks, old_tracks, args.max_age, args.time_pattern, args.scale, metric) # match bboxes to tracks (tmatch)
        # print(f' --- Tracked boxes: {len(boxes(tracks))}, {len(boxes(old_tracks))}')
    return tracks+old_tracks # sorted by time?

def strack(frames):
    """Track paired bboxes from a stereo camera"""
    pass

from parser import read_frames, show_frames
from tracking import summarize_probs, process_tracks
from definitions import bbshow, error

if __name__ == '__main__':
    g_trackno = 0

    parser = make_args_parser()
    global args
    args = parser.parse_args()

    rnheader = "frame_id\tx\ty\tw\th\tlabel\tprob"

    # Define (trivial) functions for generating output
    if args.output is None:
        def output(line):         sys.stdout.write(line+'\n')
        def tracks_output(line):  sys.stdout.write(line+'\n')
        def closeup(): pass
    else:
        of = open(args.output, 'w')
        tf = open(args.output+'.tracks', 'w')        
        def output(line):          of.write(line+'\n')
        def tracks_output(line):   tf.write(line+'\n')
        def closeup():
            of.close()
            tf.close()

    if args.consensus and args.stereo:
        error('Unsupported combination of arguments:\n'+str(args))

    ##################################################
    # Read in the detections as a stream of stereo frames
    elif args.stereo:
        if len(args.FILES) == 2:
            [fr_left, fr_right] = [read_frames(f, shape=args.shape) for f in args.FILES]
            res1 = []
            for t in zip_frames([fr_left, fr_right]):
                res1.append(merge_frames(t))
        else:
            error(f'Wrong number of files {len(args.FILES)} instead of 2.')
    ##################################################
    # Read a list of annotations to construct consensus frames
    elif args.consensus:
        fs = [read_frames(f, shape=args.shape) for f in args.FILES]
        res1 = []
        for t in zip_frames(fs):
            res1.append(consensus_frame(t))
    ##################################################
    # Just a regular annotation file/directory
    else:
        if len(args.FILES) == 1:
            res1 = read_frames(args.FILES[0], shape=args.shape)
        else:
            error(f'Too many files, consider -s or -c')

    ##################################################
    # Perform tracking
    from tracking import bbdist_track, bbdist_pair
    from definitions import frameid

    if args.track:
        # todo: if pattern/enumeration is given, insert empty frames
        if args.stereo:
            metric = bbdist_pair
            # def firstframe(t): return t.bblist[0][0].frameid if t.bblist[0][0] is not None else t.bblist[0][1].frameid
        else:
            metric = bbdist_track
        def firstframe(t): return frameid(t.bblist[0])

        ts = track(res1, metric)
        ts.sort(key=firstframe)

        # print(f'*** Created number of tracks: {len(ts)}, total bboxes {len([b for f in ts for b in f.bblist])}')

        # maybe eliminate very short tracks?
        if True:
            for x in ts:
                print(f'Track: {x.trackid}')
                for b in x.bblist:
                    print(bbshow(b))
                print('')

        fs, ss = process_tracks(ts, args.interpolate)
        output('# '+rnheader)
        for f in fs:
            for b in f.bboxes:
                output(bbshow(b))
        for s in ss:
            cls,prb,res = summarize_probs(ss[s])
            tracks_output(f'track: {s} len: {sum([len(v) for v in ss[s].values()])} prediction: {cls} prob: {prb:.5f} logits: {res}')

    elif args.stereo: # not tracking, stereo frames
        # just output res1 (::[Frame])
        dashes = '-\t'*6+'-'
        output('# '+rnheader+'\t'+rnheader+'\tsimilarity')
        for x in res1:
            for a,b in x.bboxes: # assuming -s here?
                astr = bbshow(a) if a is not None else dashes
                bstr = bbshow(b) if b is not None else dashes
                dist = str(bbdist_stereo(a,b,args.scale)) if a is not None and b is not None else "n/a"
                output(astr+"\t"+bstr+"\t"+dist)
    else:
        show_frames(res1)

    closeup()
