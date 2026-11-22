"""Explicit assignment metrics with publication-level size denominators."""
from collections import defaultdict


def average(values):return sum(values)/len(values) if values else None

def ratio(a,b):return a/b if b else None


def summarize_assignment(tasks,results):
    latest={}
    for r in results:
        latest[(r.get('owner'),r.get('arxiv_id'),r.get('step'),r.get('version'))]=r
    per_paper=[];expected=covered=unsupported=unresolved=0
    before_sizes=[];after_sizes=[];full_raw=[];full_arxiv=[];micro_good=0;macro=[]
    complete_papers=0
    for task in tasks:
        owner,aid=task['owner'],task['arxiv_id']
        meta=latest.get((owner,aid,'metadata',None),{})
        data=meta.get('data') or {}
        versions=data.get('versions',[])
        sources=[latest.get((owner,aid,'sources',v['version']),{}) for v in versions]
        expected+=len(versions)
        covered_here=sum(s.get('status') in ('success','unsupported') for s in sources)
        covered+=covered_here
        unsupported+=sum(s.get('status')=='unsupported' for s in sources)
        unresolved+=len(versions)-covered_here
        sizes=[]
        for source in sources:
            m=source.get('metrics') or {}
            pair=(m.get('extracted_bytes_before_filter'),m.get('retained_source_bytes'))
            if all(type(x) in (int,float) and x>=0 for x in pair):sizes.append(pair)
        size_ok=(bool(versions) and data.get('versions_complete') is True
                 and covered_here==len(versions) and len(sizes)==len(versions))
        before=sum(s[0] for s in sizes) if size_ok else None
        after=sum(s[1] for s in sizes) if size_ok else None
        if size_ok:before_sizes.append(before);after_sizes.append(after)
        refs=latest.get((owner,aid,'references',None),{})
        rd=refs.get('data') or {}
        refs_complete=rd.get('complete') is True
        all_count=arxiv_count=complete_count=None
        if refs_complete:
            raw=rd.get('raw_references')
            entries=rd.get('references')
            if isinstance(raw,list) and isinstance(entries,dict):
                all_count=len(raw);arxiv_count=len(entries)
                complete_count=sum(all(entry.get(k) is not None for k in
                    ('title','authors','submission_date','semantic_scholar_id')) for entry in entries.values())
                full_raw.append(all_count);full_arxiv.append(arxiv_count);micro_good+=complete_count
                if arxiv_count:macro.append(complete_count/arxiv_count)
        good=(meta.get('status')=='success' and data.get('versions_complete') is True
              and bool(versions) and covered_here==len(versions) and refs.get('status') in ('success','empty') and refs_complete)
        complete_papers+=good
        per_paper.append({'owner':owner,'arxiv_id':aid,'versions_expected':len(versions),
            'versions_covered':covered_here,'before_filter_bytes':before,'retained_bytes':after,
            'references_complete':refs_complete,'raw_references':all_count,'arxiv_references':arxiv_count,
            'complete_reference_metadata':complete_count,'paper_complete':bool(good)})
    return {'publications':len(tasks),'papers_complete':complete_papers,
        'paper_success_rate':ratio(complete_papers,len(tasks)),
        'versions':{'expected':expected,'covered':covered,'unsupported':unsupported,'unresolved':unresolved,
                    'papers_missing_version_history':sum(not (latest.get((t['owner'],t['arxiv_id'],'metadata',None),{}).get('data') or {}).get('versions_complete') for t in tasks)},
        'source_sizes':{'unit':'uncompressed bytes; sum all versions per publication',
                        'sample_publications':len(before_sizes),'excluded_publications':len(tasks)-len(before_sizes),
                        'mean_before_filter_bytes':average(before_sizes),'mean_retained_bytes':average(after_sizes)},
        'references':{'fully_fetched_papers':len(full_raw),'unfetched_or_partial_papers':len(tasks)-len(full_raw),
                      'query_success_rate':ratio(len(full_raw),len(tasks)),
                      'total_raw_references_complete_lists':sum(full_raw),
                      'mean_all_references_per_fully_fetched_paper':average(full_raw),
                      'mean_arxiv_references_per_fully_fetched_paper':average(full_arxiv),
                      'metadata_macro_success_rate':average(macro),'metadata_macro_sample_papers':len(macro),
                      'metadata_micro_success_rate':ratio(micro_good,sum(full_arxiv)),
                      'metadata_complete_arxiv_entries':micro_good,'metadata_arxiv_entries_denominator':sum(full_arxiv),
                      'metadata_policy':'Required arXiv reference fields; macro excludes empty arXiv sets; micro pools entries'},
        'per_publication':per_paper}
