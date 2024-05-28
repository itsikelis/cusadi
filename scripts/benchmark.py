import ctypes
import torch
import time
from casadi import *
from evaluateCasADiPython import evaluateCasADiPython

# Load the shared library
libc = ctypes.CDLL('../build/libc_libs.so')
libc.evaluateCasADiFunction.argtypes = [
    ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_int),

    ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_int),

    ctypes.POINTER(ctypes.c_float),
    ctypes.c_int,

    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_float),
    ctypes.c_int,
    ctypes.c_int]
libc.printOperationKeys()
libcusadi = ctypes.CDLL('../build/libcusadi.so')

assert torch.cuda.is_available()
device = torch.device("cuda")  # device object representing GPU

class CusADiFunction:
    f = None
    name = None
    device = None
    batch_size = 0

    def __init__(self, casadi_fn, batch_size, name="f", device='cuda'):
        self.f = casadi_fn
        self.name = name
        self.device = device
        self.batch_size = batch_size
        self.parseFunctionParameters(self.f)
        self.prepareFunctionData()
        self.printCasADiFunction()

    def parseFunctionParameters(self, casadi_fn):
        n_instr = f.n_instructions()
        input_idx = []
        input_idx_lengths = [0]
        output_idx = []
        output_idx_lengths = [0]
        for i in range(n_instr):
            input_idx.extend(casadi_fn.instruction_input(i))
            input_idx_lengths.append(len(casadi_fn.instruction_input(i)))
            output_idx.extend(casadi_fn.instruction_output(i))
            output_idx_lengths.append(len(casadi_fn.instruction_output(i)))

        self.f_info = {
            'n_instr': n_instr,
            'n_in': f.n_in(),
            'sz_in': [f.size1_in(i) for i in range(f.n_in())],
            'n_out': f.n_out(),
            'nnz_out': [f.nnz_out(i) for i in range(f.n_out())],
            'n_w': f.sz_w(),
            'const_instr': [f.instruction_constant(i) for i in range(n_instr)],
            'operations': [f.instruction_id(i) for i in range(n_instr)],
            'input_idx': input_idx,
            'output_idx': output_idx,
            'input_idx_lengths': input_idx_lengths,
            'output_idx_lengths': output_idx_lengths}

    def setBenchmarkingMode(self):
        '''Create additional tensors for evaluation in other modes'''
        # Pytorch evaluation tensors:
        self.output_py = [torch.zeros(N_ENVS, self.f.nnz_out(), device='cuda') for i in range(self.f.n_out())]
        self.work_tensor_py = torch.zeros(N_ENVS, self.f.sz_w(), device='cuda')
        self.const_instr_py = [f.instruction_constant(i) for i in range(self.f_info['n_instr'])]
        self.operations_py = [f.instruction_id(i) for i in range(self.f_info['n_instr'])]
        self.output_idx_py = [f.instruction_output(i) for i in range(self.f_info['n_instr'])]
        self.input_idx_py = [f.instruction_input(i) for i in range(self.f_info['n_instr'])]

        # Serial CasADi evaluation Numpy arrays:

    def prepareFunctionData(self):
        # Put data on GPU with Pytorch tensors
        self.operations = torch.tensor(self.f_info['operations'], device=self.device, dtype=torch.int32).contiguous()
        self.const_instr = torch.tensor(self.f_info['const_instr'], device=self.device, dtype=torch.float32).contiguous()
        self.input_idx = torch.tensor(self.f_info['input_idx'], device=self.device, dtype=torch.int32).contiguous()
        self.input_idx_lengths = torch.tensor(self.f_info['input_idx_lengths'], device=self.device, dtype=torch.int32).contiguous()
        self.output_idx = torch.tensor(self.f_info['output_idx'], device=self.device, dtype=torch.int32).contiguous()
        self.output_idx_lengths = torch.tensor(self.f_info['output_idx_lengths'], device=self.device, dtype=torch.int32).contiguous()
        self.work_tensor = torch.zeros(self.batch_size, self.f_info['n_w'], device=self.device, dtype=torch.float32).contiguous()
        self.outputs = [torch.zeros(self.batch_size, self.f_info['nnz_out'][i], device='cuda') for i in range(self.f.n_out())]

        # Data that needs to be accessed by C must be on the CPU
        # Passing these variables by value right now, but could use pointers instead
        self.op_C = (ctypes.c_int * len(self.f_info['operations']))(*self.f_info['operations'])
        self.const_instr_C = (ctypes.c_float * len(self.f_info['const_instr']))(*self.f_info['const_instr'])
        self.input_idx_C = (ctypes.c_int * len(self.f_info['input_idx']))(*self.f_info['input_idx'])
        self.input_idx_lengths_C = (ctypes.c_int * len(self.f_info['input_idx_lengths']))(*self.f_info['input_idx_lengths'])
        self.output_idx_C = (ctypes.c_int * len(self.f_info['output_idx']))(*self.f_info['output_idx'])
        self.output_idx_lengths_C = (ctypes.c_int * len(self.f_info['output_idx_lengths']))(*self.f_info['output_idx_lengths'])
        self.output_sz_ptr = (ctypes.c_int * len(self.f_info['nnz_out']))(*self.f_info['nnz_out'])
        self.input_sz_ptr = (ctypes.c_int * len(self.f_info['sz_in']))(*self.f_info['sz_in'])

        self.work_tensor_ptr = ctypes.cast((self.work_tensor.data_ptr()), ctypes.POINTER(ctypes.c_float))
        output_ptr = [self.castAsCPointer(self.outputs[i].data_ptr()) for i in range(self.f.n_out())]
        self.output_tensors_ptr = (ctypes.POINTER(ctypes.c_float) * self.f_info['n_out'])(*output_ptr)

    def castAsCPointer(self, ptr, type='float'):
        if type == 'int':
            return ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int))
        elif type == 'float':
            return ctypes.cast(ptr, ctypes.POINTER(ctypes.c_float))

    def printCasADiFunction(self):
        print("Received function: ", self.f)

    def evaluateVectorizedWithCUDA(self, input_batch_ptr):
        # input_batch_ptr is a list of pointers to tensors
        assert len(input_batch_ptr) == self.f.n_in()
        for i in range(self.f_info['n_out']):
            self.outputs[i] *= 0
        self.work_tensor *= 0
        libc.evaluateCasADiFunction(self.output_tensors_ptr,
                                    self.f_info['n_out'],
                                    self.output_sz_ptr,

                                    input_batch_ptr,
                                    self.f_info['n_in'],
                                    self.input_sz_ptr,
                                    
                                    self.work_tensor_ptr,
                                    self.f_info['n_w'],

                                    self.op_C,
                                    self.output_idx_C,
                                    self.output_idx_lengths_C,
                                    self.input_idx_C,
                                    self.input_idx_lengths_C,
                                    self.const_instr_C,
                                    self.f_info['n_instr'],
                                    self.batch_size)
        return self.output_tensors_ptr # [batch_size x nnz]. How should we return this? 
    
    def evaluateVectorizedWithPytorch(self, input_batch):
        evaluateCasADiPython(self.output_py,
                             self.work_tensor_py,
                             input_batch,
                             self.operations_py,
                             self.output_idx_py,
                             self.input_idx_py,
                             self.const_instr_py,
                             self.f_info['n_instr'])
        return self.output_py

    def evaluateSerialWithCasADi(self, input):
        for i in range(self.batch_size):
            f.call(input[i])

f = casadi.Function.load("../test.casadi")
f_compiled = casadi.external('test', '../test.so')

# Example data on GPU
N_ENVS = 4096
N_RUNS = 10

N_ENVS_SWEEP = [1, 5, 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000]

t_sweep_codegen = []
t_sweep_cusadi = []
t_sweep_pytorch = []
t_sweep_cpu = []
t_sweep_cpu_serial = []
t_sweep_cpu_compiled_serial = []
std_sweep_codegen = []
std_sweep_cusadi = []
std_sweep_pytorch = []
std_sweep_serial = []
std_sweep_cpu_compiled = []

t_data_transfer = []
std_data_transfer = []


for i in range(f.n_in()):
    print(f.size1_in(i))
    print(f.size2_in(i))

for i in range(len(N_ENVS_SWEEP)):
    N_ENVS = N_ENVS_SWEEP[i]

    cusadi_fn = CusADiFunction(f, N_ENVS)
    cusadi_fn.setBenchmarkingMode()

    a_device = torch.ones((N_ENVS, 192), device='cuda', dtype=torch.float32)
    b_device = torch.ones((N_ENVS, 190), device='cuda', dtype=torch.float32)
    a_ptr = cusadi_fn.castAsCPointer(a_device.data_ptr())
    b_ptr = cusadi_fn.castAsCPointer(b_device.data_ptr())
    test_ptr = [a_ptr, b_ptr]
    test_input = (ctypes.POINTER(ctypes.c_float) * len(test_ptr))(*test_ptr)

    ########## * CODEGEN vectorized benchmark * ##########
    N_ENVS = 4000


    def castAsCPointer(ptr, type='float'):
        if type == 'int':
            return ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int))
        elif type == 'float':
            return ctypes.cast(ptr, ctypes.POINTER(ctypes.c_float))
    input_1_pt = torch.ones((N_ENVS, 192), device='cuda', dtype=torch.float32).contiguous()
    input_2_pt = torch.ones((N_ENVS, 190), device='cuda', dtype=torch.float32).contiguous()
    input_1_ptr = castAsCPointer(input_1_pt.data_ptr())
    input_2_ptr = castAsCPointer(input_2_pt.data_ptr())
    input_ptrs = torch.zeros(f.n_in(), device='cuda', dtype=torch.int64).contiguous()
    input_ptrs[0] = input_1_pt.data_ptr()
    input_ptrs[1] = input_2_pt.data_ptr()
    fn_input = castAsCPointer(input_ptrs.data_ptr(), 'int')


    input_tensors = [torch.ones(N_ENVS, f.nnz_in(i), device='cuda', dtype=torch.float32).contiguous()
                 for i in range(f.n_in())]
    input_ptrs = torch.zeros(f.n_in(), device='cuda', dtype=torch.int64).contiguous()
    for i in range(f.n_in()):
        input_ptrs[i] = input_tensors[i].data_ptr()
    fn_input = castAsCPointer(input_ptrs.data_ptr(), 'int')


    work_tensor = torch.zeros(N_ENVS, f.sz_w(), device='cuda', dtype=torch.float32).contiguous()
    fn_work = ctypes.cast((work_tensor.data_ptr()), ctypes.POINTER(ctypes.c_float))

    outputs = [torch.zeros(N_ENVS, f.nnz_out(i), device='cuda', dtype=torch.float32) for i in range(f.n_out())]
    output_ptrs = torch.zeros(f.n_out(), device='cuda', dtype=torch.int64).contiguous()
    for i in range(f.n_out()):
        output_ptrs[i] = outputs[i].data_ptr()
    fn_output = castAsCPointer(output_ptrs.data_ptr(), 'int')

    print(output_ptrs)
    print(fn_output)
    print(len(outputs))
    print(outputs[0].size())

    def evaluateCodegen():
        for i in range(f.n_out()):
            outputs[i] *= 0
        # work_tensor *= 0
        libcusadi.evaluate(fn_input,
                        fn_work,
                        fn_output,
                        N_ENVS)
    t_codegen = []
    for i in range(N_RUNS):
        t0 = time.time()
        evaluateCodegen()
        torch.cuda.synchronize()
        duration = time.time() - t0
        t_codegen.append(duration)
    print("Codegen wall time: ", np.mean(t_codegen))
    t_sweep_codegen.append(np.mean(t_codegen))
    std_sweep_codegen.append(np.std(t_codegen))



    ########## * CUDA vectorized benchmark * ##########
    t_cuda = []
    for i in range(N_RUNS):
        t0 = time.time()
        cusadi_fn.evaluateVectorizedWithCUDA(test_input)
        torch.cuda.synchronize()
        duration = time.time() - t0
        t_cuda.append(duration)
    print("CUDA wall time: ", np.mean(t_cuda))
    t_sweep_cusadi.append(np.mean(t_cuda))
    std_sweep_cusadi.append(np.std(t_cuda))


    ########## * Pytorch vectorized benchmark * ##########
    pytorch_input = [torch.ones((N_ENVS, 192), device='cuda', dtype=torch.float32),
                    torch.ones((N_ENVS, 190), device='cuda', dtype=torch.float32)]
    t_pytorch = []
    for i in range(N_RUNS):
        t0 = time.time()
        cusadi_fn.evaluateVectorizedWithPytorch(pytorch_input)
        torch.cuda.synchronize()
        duration = time.time() - t0
        t_pytorch.append(duration)
    print("Pytorch wall time: ", np.mean(t_pytorch))
    t_sweep_pytorch.append(np.mean(t_pytorch))
    std_sweep_pytorch.append(np.std(t_pytorch))

    ########## * Serial CPU benchmark * ##########
    output_numpy = numpy.zeros((N_ENVS, f.nnz_out()))
    t_serial = []
    t_data = []
    input_val = [numpy.ones((192, 1)), \
                 numpy.ones((190, 1))]
    for j in range(N_RUNS):
        time_start = time.time()
        for i in range(N_ENVS):
            output_numpy[i, :] = (f.call(input_val))[0].nonzeros()
        time_data_gpu = time.time()
        test = torch.from_numpy(output_numpy).to('cuda')
        t_data.append(time.time() - time_data_gpu)
        duration = time.time() - time_start
        t_serial.append(duration)
        
    print("Computation time for ", N_ENVS, " environments serially evaluated: ", np.mean(t_serial))
    t_sweep_cpu.append(np.mean(t_serial)/16)
    t_sweep_cpu_serial.append(np.mean(t_serial))
    t_data_transfer.append(np.mean(t_data))
    std_data_transfer.append(np.std(t_data))
    std_sweep_serial.append(np.std(t_serial))

    ########## * Compiled CPU benchmark * ##########
    output_numpy = numpy.zeros((N_ENVS, f.nnz_out()))
    t_compiled = []
    t_data = []
    input_val = [numpy.ones((192, 1)), \
                 numpy.ones((190, 1))]
    for j in range(N_RUNS):
        time_start = time.time()
        for i in range(N_ENVS):
            output_numpy[i, :] = (f_compiled.call(input_val))[0].nonzeros()
        time_data_gpu = time.time()
        test = torch.from_numpy(output_numpy).to('cuda')
        t_data.append(time.time() - time_data_gpu)
        duration = time.time() - time_start
        t_compiled.append(duration)
    print("Computation time for ", N_ENVS, " environments codegen serially evaluated: ", np.mean(t_compiled))
    t_data_transfer.append(np.mean(t_data))
    std_data_transfer.append(np.std(t_data))
    t_sweep_cpu_compiled_serial.append(np.mean(t_compiled))
    std_sweep_cpu_compiled.append(np.std(t_compiled))


# PLOTTING
import matplotlib.pyplot as plt

t_sweep_cpu_compiled_parallel = (np.array(t_sweep_cpu_compiled_serial)/16).tolist()
t_means = [t_sweep_codegen, t_sweep_cusadi, t_sweep_pytorch, t_sweep_cpu_compiled_parallel, t_sweep_cpu_compiled_serial]
t_stds = [std_sweep_codegen, std_sweep_cusadi, std_sweep_pytorch, std_sweep_cpu_compiled, std_sweep_cpu_compiled]

t_max = max([sublist[-1] for sublist in t_means])


# Plotting
plt.figure(figsize=(10, 6))
for i in range(len(t_means)):
    plt.plot(log10(N_ENVS_SWEEP), t_means[i], marker='o', linestyle='-', label=f'Line {i+1}')
    plt.errorbar(log10(N_ENVS_SWEEP), t_means[i], yerr=t_stds[i], fmt='none', capsize=5, ecolor='black')

plt.xlabel('Number of environments (log10)')
plt.ylabel('Evaluation Time (s)')
plt.title('Function Evaluation Time Benchmarking')
plt.legend(['CUDA + codegen', 'CUDA', 'Pytorch', 'CPU (compiled + "parallel")', 'CPU (compiled + serial)'])
plt.show()

# Normalized
# plt.figure(figsize=(10, 6))
# for i in range(len(t_means)):
#     plt.plot(log10(N_ENVS_SWEEP), 100*t_means[i]/t_max, marker='o', linestyle='-', label=f'Line {i+1}')

# plt.xlabel('Number of environments (log10)')
# plt.ylabel('Percentage of serial evaluation time (%)')
# plt.title('Function Evaluation Time Benchmarking')
# plt.legend(['CUDA + codegen', 'CUDA', 'Pytorch', 'CPU (compiled + "parallel")', 'CPU (compiled + serial)'])
# plt.show()

# Data transfer time
t_data_transfer = t_data_transfer[0:11]
std_data_transfer = std_data_transfer[0:11]
plt.figure(figsize=(10, 6))
plt.plot(log10(N_ENVS_SWEEP), t_data_transfer, marker='o', linestyle='-', label=f'Line {i+1}')
plt.errorbar(log10(N_ENVS_SWEEP), t_data_transfer, yerr=std_data_transfer, fmt='none', capsize=5, ecolor='black')
plt.xlabel('Number of environments (log10)')
plt.ylabel('Data transfer time from CPU to GPU (s)')
plt.title('Data Transfer Time')
plt.show()

t_data_transfer_percentage = [100*t_data_transfer[i]/t_sweep_cpu_compiled_serial[i] for i in range(len(t_sweep_cpu_compiled_serial))]

plt.figure(figsize=(10, 6))
plt.plot(log10(N_ENVS_SWEEP), t_data_transfer_percentage, marker='o', linestyle='-', label=f'Line {i+1}')
plt.xlabel('Number of environments (log10)')
plt.ylabel('Percentage of data transfer time (%)')
plt.title('Data Transfer Time (%)')
plt.show()






















# sparse_eval = (f.call(input_val))[0]
# plt.spy(sparse_eval)
# plt.title('Sparsity pattern of dg/dx')
# plt.savefig('boxplot_function_evaluation_time.png')
