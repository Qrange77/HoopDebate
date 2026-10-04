"""Public execution events: tool activity and stages, never model reasoning text."""
import json


class ActivityLog(list):
    def __init__(self, emit=None):
        super().__init__()
        self.emit = emit or (lambda event: None)
        self.pending = []
        self.sequence = 0

    def phase(self, label):
        self.emit({'type':'phase', 'label':label})

    def start(self, name, args, attempt_id=None):
        self.sequence += 1
        call = {'id':str(self.sequence), 'name':name, 'args':args, 'status':'running'}
        if attempt_id is not None:
            call['attempt_id'] = attempt_id
        self.pending.append(call)
        self.emit({'type':'tool_start', 'call':dict(call)})
        return call

    def append(self, item):
        call = next((c for c in self.pending if c['name']==item['name']), None)
        if call is None:
            call = self.start(item['name'],item.get('args',{}))
        self.pending.remove(call)
        result = item.get('result')
        try:
            parsed = json.loads(result) if isinstance(result,str) else result
        except (ValueError,TypeError):
            parsed = None
        failed = isinstance(parsed,dict) and (bool(parsed.get('error')) or parsed.get('valid') is False or parsed.get('supported') is False or parsed.get('status') == 'unavailable')
        status = 'failed' if failed else 'completed'
        if not failed and isinstance(parsed,dict) and parsed.get('status') in ('revise','needs_evidence'):
            status = 'needs_revision' if parsed['status'] == 'revise' else 'needs_evidence'
        finished = dict(item, id=call['id'], status=status)
        if call.get('attempt_id'):
            finished['attempt_id'] = call['attempt_id']
        elif isinstance(parsed, dict) and parsed.get('attempt_id'):
            finished['attempt_id'] = parsed['attempt_id']
        super().append(finished)
        self.emit({'type':'tool_end','call':finished})

    def fail_pending(self, message):
        for call in list(self.pending):
            self.append({'name':call['name'],'args':call['args'],'result':json.dumps({'error':message})})
