"""Score predicted windows against explicitly human-reviewed source intervals.
Usage: python scripts/score_clip_benchmark.py predictions.json labels.json
Unreviewed intervals are excluded; this is not whole-video recall.
"""
import json,sys,math
from pathlib import Path

def iou(a,b):
    overlap=max(0,min(a['end'],b['end'])-max(a['start'],b['start']))
    union=max(a['end'],b['end'])-min(a['start'],b['start'])
    return overlap/union if union else 0

def score(predictions,labels,threshold=.5):
    for row in predictions+labels:
        if not all(isinstance(row.get(k),(int,float)) and math.isfinite(row[k]) for k in ('start','end')) or row['end']<=row['start']:
            raise ValueError('Intervals must be finite and have end > start')
    reviewed=[r for r in labels if r.get('rating') in ('publish','reject')]
    good=[r for r in reviewed if r['rating']=='publish']
    covered=[p for p in predictions if any(p['source']==r['source'] and iou(p,r)>=threshold for r in reviewed)]
    matches=[];used_p=set();used_r=set()
    edges=sorted([(iou(p,r),i,j) for i,p in enumerate(covered) for j,r in enumerate(good) if p['source']==r['source'] and iou(p,r)>=threshold],reverse=True)
    for overlap,i,j in edges:
        if i in used_p or j in used_r:continue
        used_p.add(i);used_r.add(j);matches.append((covered[i],good[j]))
    title_labels=[r for r in labels if r.get('title_accuracy') in ('good','bad')]
    boundary_labels=[r for r in labels if r.get('boundaries') in ('good','bad')]
    return {'reviewed_predictions':len(covered),'unrated_predictions':len(predictions)-len(covered),'publishable_references':len(good),'matched':len(matches),
        'precision_on_reviewed':len(matches)/len(covered) if covered else None,
        'recall_on_labeled_positives':len(matches)/len(good) if good else None,
        'mean_boundary_error_seconds':sum(abs(p['start']-r['start'])+abs(p['end']-r['end']) for p,r in matches)/(2*len(matches)) if matches else None,
        'reviewed_titles':len(title_labels), 'title_accuracy_on_reviewed':sum(r['title_accuracy']=='good' for r in title_labels)/len(title_labels) if title_labels else None,
        'reviewed_boundaries':len(boundary_labels), 'complete_boundaries_on_reviewed':sum(r['boundaries']=='good' for r in boundary_labels)/len(boundary_labels) if boundary_labels else None,
        'note':'Human-reviewed intervals only; not full-video recall or a virality estimate.'}

if __name__=='__main__':
    print(json.dumps(score(json.loads(Path(sys.argv[1]).read_text()),json.loads(Path(sys.argv[2]).read_text())),indent=2))
