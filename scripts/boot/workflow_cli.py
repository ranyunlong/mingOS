"""Send one bounded developer command to the VM's private input owner."""
import argparse
import json
from pathlib import Path
from workflow_control import command_steps, request

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control',type=Path,required=True)
    commands=parser.add_subparsers(dest='op',required=True)
    commands.add_parser('status'); commands.add_parser('capture')
    for name in ('click','power'):
        child=commands.add_parser(name); child.add_argument('x',type=int); child.add_argument('y',type=int)
    child=commands.add_parser('type'); child.add_argument('text')
    child=commands.add_parser('key'); child.add_argument('qcode'); child.add_argument('--modifiers',nargs='*',default=[])
    args=parser.parse_args(); command=vars(args).copy(); command.pop('control')
    command_steps(command)
    if args.op=='status': result=request(args.control,command)
    else:
        # Motion frames may advance revision; stable layout and input ownership remain bound.
        for attempt in range(3):
            view=request(args.control,{'op':'status'})
            if not view.get('state'): raise ValueError('desktop state has not arrived')
            result=request(args.control,command,view)
            if result.get('status')!='rejected' or 'current session/layout/input epoch' not in result.get('error',''): break
    print(json.dumps(result,indent=2))
    return 1 if result.get('status')=='rejected' else 0

if __name__=='__main__': raise SystemExit(main())
