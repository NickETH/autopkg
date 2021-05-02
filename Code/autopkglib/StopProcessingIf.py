#!/usr/local/autopkg/python
#
# Copyright 2013 Greg Neagle
#
# Inital Windows support by Nick McSpadden, 2018
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# 20190328 Nick Heim: Adaption for Windows. Very basic so far. Looking for a better predicate class...
# 20210417 Nick Heim: Port Windows adaption to V2.3.1

"""See docstring for StopProcessingIf class"""

from autopkglib import Processor, ProcessorError, log, is_mac, is_windows

if is_mac():  # Added on Windows version
    try:
        from Foundation import NSPredicate
    except ImportError:
        log("WARNING: Failed 'from Foundation import NSPredicate' in " + __name__)

__all__ = ["StopProcessingIf"]


class StopProcessingIf(Processor):
    """Sets a variable to tell AutoPackager to stop processing a recipe if a
    predicate comparison evaluates to true."""

    description = __doc__
    input_variables = {
        "predicate": {
            "required": True,
            "description": (
                "NSPredicate-style comparison against an environment key. See "
                "http://developer.apple.com/library/mac/#documentation/"
                "Cocoa/Conceptual/Predicates/Articles/pSyntax.html"
            ),
        }
    }
    output_variables = {
        "stop_processing_recipe": {
            "description": "Boolean. Should we stop processing the recipe?"
        }
    }

    def predicate_evaluates_as_true(self, predicate_string):
        if is_mac():
            """Evaluates predicate against our environment dictionary"""
            try:
                predicate = NSPredicate.predicateWithFormat_(predicate_string)
            except Exception as err:
                raise ProcessorError(f"Predicate error for '{predicate_string}': {err}")

            result = predicate.evaluateWithObject_(self.env)
            self.output(f"({predicate_string}) is {result}")
            return result
        elif is_windows(): # Added on Windows version
            try:
                download_changed = self.env.get('download_changed')
                result = eval(predicate_string)
                # print >> sys.stdout, "predicate %s" % result
            except Exception as err:
                raise ProcessorError(f"Predicate error for '{predicate_string}': {err}")

            self.output(f"({predicate_string}) is {result}")
        return result

    def main(self):
        self.env["stop_processing_recipe"] = self.predicate_evaluates_as_true(
            self.env["predicate"]
        )


if __name__ == "__main__":
    PROCESSOR = StopProcessingIf()
    PROCESSOR.execute_shell()
