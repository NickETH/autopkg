# coding: utf-8
import subprocess
import os

from autopkglib import Processor
from datetime import datetime

__all__ = ["RunPowerShell"]

class RunPowerShell(Processor):
    description = "Execute a PowerShell script."
    input_variables = {
        "script_path": {
            "required": True,
            "description": "Path of the script.",
        },
        "powershell_arguments": {
            "required": True,
            "description": "Arguments for the script.",
        },
    }
    output_variables = {
        "runpowershell_summary_result": {
            "description": "Executed command."
        },
    }

    __doc__ = description

    def main(self):
        # Clear any pre-exising summary result
        if 'runpowershell_summary_result' in self.env:
            del self.env['runpowershell_summary_result']
        
        script_path = self.env.get('script_path')
        powershell_arguments = self.env.get('powershell_arguments')
        
        # Call the powershell script with its arguments.
        powershell = "C:\\windows\\SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe"
        cmd = [powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               script_path]
        for key, value in powershell_arguments.items():
            cmd.append(f"-{key}")
            cmd.append(f"{value}")

        output = subprocess.getstatusoutput(cmd)
        
        # Summary
        cmd_string = " ".join(cmd)
        self.env["runpowershell_summary_result"] = {
            'summary_text': 'The following commands were executed:',
            'report_fields': ['command', 'status'],
            'data': {
                'command': cmd_string,
                'status': str(output[0])
            }
        }

if __name__ == '__main__':
    processor = RunPowerShell()
    processor.execute_shell()