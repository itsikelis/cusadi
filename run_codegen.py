import os
import argparse
from casadi import *
from cusadi import *

def main(args):
    casadi_fns = []
    fn_dir = CUSADI_BENCHMARK_DIR if args.codegen_benchmark_fns else CUSADI_FUNCTION_DIR
    for filename in os.listdir(fn_dir):
        f = os.path.join(CUSADI_FUNCTION_DIR, filename)
        if os.path.isfile(f) and f.endswith(".casadi"):
            if args.fn_name == "all" or args.fn_name in f:
                print("CasADi function found: ", f)
                casadi_fns.append(casadi.Function.load(f))
    for f in casadi_fns:
        generateCUDACode(f)
        if args.gen_pytorch:
            generatePytorchCode(f)
    generateCMakeLists(casadi_fns)
    compileCUDACode()

# Helper functions
def compileCUDACode():
    print("Compiling CUDA code...")
    status = os.system("cd build && cmake .. && make -j")
    if status == 0:
        print("Compilation complete.")
    else:
        print("Compilation failed.")
        exit(1)
        
def printParserArguments(parser, args):
    # Print out all arguments, descriptions, and default values in a formatted manner
    print(f"\n{'Argument':<20} {'Description':<80} {'Default':<10} {'Current Value':<10}")
    print("=" * 140)
    for action in parser._actions:
        if action.dest == 'help':
            continue
        arg_strings = ', '.join(action.option_strings)
        description = action.help or 'No description'
        default = action.default if action.default is not argparse.SUPPRESS else 'No default'
        current_value = getattr(args, action.dest, default)
        print(f"{arg_strings:<20} {description:<80} {default:<10} {current_value:<10}")
    print()

def setupParser():
    parser = argparse.ArgumentParser(description='Script to generate parallelized code from CasADi functions')
    parser.add_argument('--fn', type=str, dest='fn_name', default='all',
                        help='Function to parallelize in cusadi/casadi_functions, defaults to "all"')
    parser.add_argument('--gen_CUDA', type=bool, dest='gen_CUDA', default=True,
                        help='Generate CUDA codegen. Defaults to True')
    parser.add_argument('--gen_pytorch', type=bool, dest='gen_pytorch', default=False,
                        help='Generate Pytorch codegen in addition to CUDA. Defaults to False')
    parser.add_argument('--benchmark', type=bool, dest='codegen_benchmark_fns', default=False,
                        help='Generate functions for benchmarking. Defaults to False')
    return parser

if __name__ == "__main__":
    parser = setupParser()
    args = parser.parse_args()
    printParserArguments(parser, args)
    main(args)