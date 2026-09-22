"""Request-local inventory of source-derived work, independent of application type."""
import json
import re

from .contracts import TEXT,TEXTS,object_schema
from .conversation import _write
from .research_tools import ReadTool,ToolInputError

ITEM=object_schema({'id':TEXT,'requirement':TEXT,
                    'kind':{'type':'string','enum':['answer','action','handoff']}})
SCHEMA=object_schema({'items':{'type':'array','items':ITEM},'source_receipts':TEXTS})


class WorkInventory:
    def __init__(self,directory,receipts):
        self.path=directory/'work-inventory.json'
        self.receipts=receipts

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'items':[],'source_receipts':[]}

    def track(self,items,source_receipts):
        if not 1<=len(items)<=20 or len(source_receipts)>40:
            raise ToolInputError('Track one to twenty distinct requested results and up to forty source receipts.')
        previous=self.read();old={item['id']:item for item in previous['items']};new={}
        for item in items:
            if (not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}',item['id']) or item['id'] in new
                    or not item['requirement'].strip() or len(item['requirement'])>500):
                raise ToolInputError('Use unique short item IDs and nonempty requirements under 500 characters.')
            if item['id'] in old and old[item['id']]!=item:
                raise ToolInputError('Keep existing requirements unchanged; owner corrections use a new continuation.')
            new[item['id']]=item
        if not old.keys()<=new.keys():
            raise ToolInputError('Do not drop tracked work. Retain unfinished items in the inventory.')
        for index in source_receipts:
            if (not index.isdigit() or len(index)>3 or int(index)>=len(self.receipts)
                    or 'result' not in self.receipts[int(index)] or self.receipts[int(index)].get('error')
                    or self.receipts[int(index)].get('uncertain')):
                raise ToolInputError('Reference successful current source receipt indexes only.')
        value={'items':items,'source_receipts':list(dict.fromkeys(previous['source_receipts']+source_receipts))}
        _write(self.path,value)
        return {**value,'coverage':'Declared work only, not proof of source completeness or authority to act.'}

    def tool(self):
        return ReadTool('work.track','Track the full requested set before executing a multi-item request. '
            'Read source material first, then declare one stable ID per requested result, its requirement and kind. '
            'Use current source receipt indexes (empty only for work directly specified by the owner). '
            'Include all existing items when expanding the set; tracked work cannot silently disappear. '
            'At finish include item_id on each corresponding outcome. This is local bookkeeping, never external-action permission.',
            SCHEMA,self.track)
