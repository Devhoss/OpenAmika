"""Confirmed, idle-only active-memory reset for the isolated Build F.

No provider calls. Recovery copies are local; this is not secure deletion.
"""
import contextlib
from datetime import datetime, timezone
import functools
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush(); os.fsync(handle.fileno())
        name=handle.name
    os.replace(name, path)


def install_reset(g, memory, meter, worker_lock, work_requested):
    brain=Path(g['__file__']).resolve().parent
    root=brain.parent
    storage=(root/'memory_f').resolve()
    profile=memory.folder.name
    if profile not in ('live','validation'):raise ValueError('Unexpected memory profile')
    pending=storage/('reset_pending_'+profile+'.json')
    gate=threading.RLock()
    activity={'count':0,'failed':False}
    paths={}
    for key,name in (('MEMORY_FILE','conversations.json'),('CONSOLIDATED_FILE','consolidated.json'),('THINKING_LOG_FILE','thinking_log.json')):
        path=Path(g.get(key,Path('memories')/name))
        if not path.is_absolute():path=brain/path
        path=path.resolve()
        if path != (brain/'memories'/name).resolve():raise ValueError('Unexpected context path')
        paths[name]=path

    def clear_context():
        for name,path in paths.items():_atomic(path,{'turns':[]} if name=='conversations.json' else {'entries':[]} if name=='thinking_log.json' else {'memories':[]})
        for key in ('CONVERSATION_HISTORY','CONSOLIDATED_MEMORIES','THINKING_LOG','_COMPLETED_CPS'):
            if key in g:g[key].clear()
        g['_pending_think']=None
        g['_pending_seq']=g.get('_pending_seq',0)+1
        g['_CURRENT_TURN_NUM']=0
        if '_init_current_cp' in g:g['_init_current_cp']()

    def finish_transaction(transaction):
        backup=(storage/'backups'/transaction['backup_id']).resolve()
        if backup.parent != (storage/'backups').resolve():raise ValueError('Invalid backup path')
        # A runtime wipe has its own recovery journal. It can complete during
        # MemoryRuntime startup before this cross-store journal resumes.
        marker=memory.folder/'reset.json'
        reset=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
        already=(str(backup)==reset.get('backup_dir') and reset.get('phase')=='complete')
        if transaction.get('phase')!='memory_cleared' and not already:
            memory.wipe(backup)
        transaction['phase']='memory_cleared';_atomic(pending,transaction)
        clear_context()
        work_requested.clear()
        transaction['phase']='complete'
        _atomic(backup/'reset_receipt.json',transaction)
        # Remove only this exact transaction marker; all evidence is backed up.
        pending.unlink()
        return backup

    if pending.exists():
        # Main has not loaded recent-context files yet. Finish a confirmed reset
        # before an old prompt or historical seed can become active again.
        finish_transaction(json.loads(pending.read_text(encoding='utf-8')))

    @contextlib.contextmanager
    def operation():
        with gate:
            if activity['failed']:raise RuntimeError('Memory reset needs recovery; restart Build F.')
            activity['count']+=1
        try:yield
        finally:
            with gate:activity['count']-=1

    for name in ('_ws_run_generation','run_thinking_once'):
        original=g[name]
        @functools.wraps(original)
        def wrapped(*args,_original=original,**kwargs):
            with operation():return _original(*args,**kwargs)
        g[name]=wrapped
    for name in ('_start_message_gen','_start_inject_gen','trigger_thinking_async'):
        if name not in g:continue
        original=g[name]
        @functools.wraps(original)
        def dispatch(*args,_original=original,**kwargs):
            with gate:
                if activity['failed']:raise RuntimeError('Memory reset needs recovery; restart Build F.')
                return _original(*args,**kwargs)
        g[name]=dispatch

    def reset_memory():
        from f_memory import MemoryBusyError
        with gate:
            if activity['failed']:return {'ok':False,'error':'Memory reset needs recovery; restart Build F.'},503
            if activity['count'] or g.get('_thinking_inflight',0):
                return {'ok':False,'error':'Amika is busy. Wait until speech and memory updates finish.'},409
            with g['_ws_active_lock']:
                if g['_ws_active']:return {'ok':False,'error':'A conversation is still active.'},409
            # A request that began but never finished (its thread died) would count as active forever and
            # refuse every wipe (2026-11-01): retire anything older than the longest drain deadline first.
            sweep=getattr(meter,'sweep_stale',None)
            if callable(sweep):sweep()
            if meter.summary()['active_requests'] or not worker_lock.acquire(blocking=False):
                return {'ok':False,'error':'A model or memory update is still active.'},409
            try:
                with g['_hist_lock'], g.get('_think_lock',contextlib.nullcontext()):
                    backup_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:12]
                    backup=storage/'backups'/backup_id
                    (backup/'brain_context').mkdir(parents=True,exist_ok=False)
                    for name,path in paths.items():
                        if path.exists():shutil.copy2(path,backup/'brain_context'/name)
                    transaction={'profile':profile,'backup_id':backup_id,'phase':'prepared','confirmed_at':datetime.now(timezone.utc).isoformat()}
                    _atomic(pending,transaction)
                    finish_transaction(transaction)
                    return {'ok':True,'backup_id':backup_id,'memory':memory.status(),'usage':meter.summary()},200
            except MemoryBusyError:
                # No runtime data changed if its concurrency gate refused reset.
                if pending.exists():pending.unlink()
                return {'ok':False,'error':'Memory work is busy. Try after it finishes.'},409
            except Exception:
                activity['failed']=True
                return {'ok':False,'error':'Reset could not finish. Your recovery backup is retained; restart Build F to recover.'},503
            finally:worker_lock.release()

    @g['app'].route('/api/build_f/wipe',methods=['POST'])
    def wipe_route():
        request=g['request']
        if request.headers.get('Origin'):
            return g['jsonify']({'ok':False,'error':'Use the OpenAmika page to confirm this action.'}),403
        if request.headers.get('X-Build-F-Action')!='wipe-memory':
            return g['jsonify']({'ok':False,'error':'Explicit wipe action required.'}),403
        data=request.get_json(silent=True) if request.is_json else None
        if not isinstance(data,dict) or data.get('confirmation')!='WIPE':
            return g['jsonify']({'ok':False,'error':'Type WIPE to confirm.'}),400
        result,code=reset_memory()
        return g['jsonify'](result),code

    if 'clear_memory' in g['app'].view_functions:
        g['app'].view_functions['clear_memory']=lambda:(g['jsonify']({'ok':False,'error':'Use the confirmed Build F wipe control.'}),409)
    g['F_RESET_MEMORY']=reset_memory
