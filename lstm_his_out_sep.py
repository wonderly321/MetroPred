# Author: h12345jack
import os
import time
import datetime
import math
import numpy as np
import torch
import torch.nn as nn
import torch.utils.data as Data
import torch.nn.functional as F
from tensorboardX import SummaryWriter
from sklearn.metrics import mean_absolute_error
cuda_device = torch.device('cuda:2')

LEARNING_RATE = 0.01
WEIGHT_DECAY = 0.001
epochs = 801
batch_size = 128
torch.manual_seed(13)

CONTAIN_25 = 24

os.system('rm -rf tb_output/fangzhong/*')
writer = SummaryWriter(log_dir='tb_output/fangzhong')
#####################################################################################################
dataset_path = './dataset/h_train'
val = [24, 25]
datas = []
noises = [1, 5, 6, 12, 13, 19, 20]
################
# cal avg
##############
avg = np.zeros((144, 81, 2))
for i in range(1, 25): # 1..25 means cal avg without date 25
#for i in [4, 11, 18]:
    if i in noises: continue
    fpath = os.path.join(dataset_path, '{}.npy'.format(i))
    data = np.load(fpath)
    avg += data
avg /= 17
avg = avg.swapaxes(0,1)
avg = torch.from_numpy(avg).float().cuda(cuda_device)
################
# 
for i in range(25):
    fpath = os.path.join(dataset_path, '{}.npy'.format(i + 1))
    data = np.load(fpath)
    data = torch.from_numpy(data)
    data = torch.einsum("ijk->jik", data)
    ids = torch.from_numpy(np.array([np.ones(144)*i for i in range(81)])).double()
    ids = ids.view(81, 144, 1)
    data = torch.cat([data, ids], dim=2)
    datas.append(data)
####################################################################################################
#
class BiLSTM(nn.Module):
    def __init__(self):
        super(BiLSTM, self).__init__()
        bidirectional = True
        dim = 128
        linear_dim = dim * 2 if bidirectional else dim
        self.rnn = nn.LSTM(2, dim,
                           num_layers = 3,
                           batch_first=True,
                           bidirectional=bidirectional,
                           dropout=0.3
                  )
        self.none_linear_layer = nn.Sequential(
            nn.Linear(linear_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 2),
            nn.ReLU()
        )

    def forward(self, data):
        result, _ = self.rnn(data)
        result = self.none_linear_layer(result)
        return result

class GLSTM(nn.Module):
    def __init__(self, adj_mx):
        super(GLSTM, self).__init__()
        dim = 128
        self.rnn = nn.LSTM(1, dim, num_layers=2, batch_first=True)
        self.fc = nn.Sequential(
            nn.Linear(dim, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.ReLU()
        )
        self.weights = torch.nn.Parameter(torch.FloatTensor(81,81))

        self.weights.data = adj_mx

    def forward(self, g_data): # bs*12*81*1 -> [bs*1*1] * 81
        # g_data: bs * 12 * 81 * 1
        # graph: 81 * 81
        g_data = g_data.view(-1, 12, 81)
        ret = torch.zeros(g_data.shape[0], 1, 81, 1).float().cuda(cuda_device)
        for i in range(81):
            freqs = self.weights[:, i].view(81,1) # 81 * 1
            income = g_data.matmul(freqs) # bs * 12 * 1
            res, _ = self.rnn(income)
            outgoing = self.fc(res)
            ret[:,:,i,:] = outgoing[:, -1, :].view(g_data.shape[0], 1, 1)
        return ret

def train_glstm():
    X = []
    for i in range(CONTAIN_25): # [0..23] ==> date 1 .. data 24
        a = datas[i] # 81 * 144 * 3
        a = torch.einsum("ijk->jik", a) # 144 * 81 * 3
        X.append(a)
    all_data = torch.cat(X, dim=0).float() # ? * 81 * 3
    tot = all_data.shape[0]
    X = []
    Y = []
    for i in range(tot-13):
        a = all_data[i:i+12,:,:]
        b = all_data[i+12, :, :]
        X.append(a.view(1,12,81,3))
        Y.append(b.view(1,1,81,3))
    tot = len(X)
    
    all_data_X = torch.cat(X[:int(tot * 0.8)], dim=0).float().cuda(cuda_device) # ? * 12 * 81 * 3
    all_data_Y = torch.cat(Y[:int(tot * 0.8)], dim=0).float().cuda(cuda_device) # ? * 1 * 81 * 3
    val_data_X = torch.cat(X[int(tot * 0.8):], dim=0).float().cuda(cuda_device)
    val_data_Y = torch.cat(Y[int(tot * 0.8):], dim=0).float().cuda(cuda_device)
    
    torch_dataset = Data.TensorDataset(all_data_X[:,:,:,:1], all_data_Y[:,:,1:2])
    loader = Data.DataLoader(
        dataset = torch_dataset,
        batch_size = batch_size,
        shuffle = True,
        num_workers= 0
    )

    adj_mx = np.load('./maps/freqg_all.npy')
    for i in range(81):
        if i != 54:
            adj_mx[:, i] = adj_mx[:, i] / np.sum(adj_mx[:, i])
        else:
            adj_mx[:, i] = 0
        adj_mx[i,i] = 0
    adj_mx = torch.from_numpy(adj_mx).float().cuda(cuda_device)

    model = GLSTM(adj_mx)
    model.cuda(cuda_device)

    crition = nn.L1Loss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)

    for epoch in range(epochs):
        total_loss = []
        time1 = time.time()

        model.train()

        for step, (batch_x, batch_y) in enumerate(loader):
            optimizer.zero_grad()

            X = batch_x
            y = batch_y
            pred_y = model(X)
            loss = crition(pred_y, y)
            total_loss.append(loss.data.cpu().numpy())
            loss.backward()
            optimizer.step()

        model.eval()
        val_X = val_data_X[:,:,:,:1]
        val_y = val_data_Y[:,:,:,1:2]
        
        pred_y = model(val_X)
        val_loss = crition(pred_y, val_y)

        train_loss = np.mean(total_loss)
        val_loss = val_loss.data.cpu().numpy().mean()
        print("Epoch", epoch,'train loss:', train_loss)
        print("Epoch", epoch,'validation loss:', val_loss )
        print(time.time() - time1)

class Model(nn.Module):
    def __init__(self, adj_mx):
        super(Model, self).__init__()
        self.weights = torch.nn.Parameter(torch.FloatTensor(144,2))
        self.bias = torch.nn.Parameter(torch.FloatTensor(144,2))
        self.bi_lstm = BiLSTM()

        stdv = 1. / math.sqrt(self.weights.size(1))
        self.weights.data.uniform_(-stdv, stdv)
        self.bias.data.uniform_(-stdv, stdv)

        self.g_lstm = GLSTM(adj_mx)

    def forward(self, data, avg):
        result = self.bi_lstm(data[:,:,:2]) # shape: bs*144*2
        sid = data[:,0,2].long()
        result = result * (1-self.weights) + avg[sid] * self.weights + self.bias # bs * 144 * 2
        
        return result

def main():
    X = []
    for i in range(CONTAIN_25 - 1):
        if i + 1 in noises: continue
        if i + 2 in noises: continue
        a = datas[i]
        b = datas[i + 1]
        c = torch.cat((a, b), dim=2)
        X.append(c)

    all_data = torch.cat(X, dim=0).float().cuda(cuda_device)

    a = datas[val[0] - 1]
    b = datas[val[1] - 1]
    val_data = torch.cat((a, b), dim=2).float().cuda(cuda_device)


    torch_dataset = Data.TensorDataset(all_data[:,:,:3], all_data[:,:,3:5])
    loader = Data.DataLoader(
        dataset = torch_dataset,
        batch_size = batch_size,
        shuffle = True,
        num_workers= 0
    )

    adj_mx = np.load('./maps/freqg_all.npy')
    for i in range(81):
        if i != 54:
            adj_mx[:, i] = adj_mx[:, i] / np.sum(adj_mx[:, i])
        else:
            adj_mx[:, i] = 0
    adj_mx = torch.from_numpy(adj_mx).float().cuda(cuda_device)

    #model = BiLSTM()
    model = Model()
    model.cuda(cuda_device)

    crition = nn.L1Loss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)

    for epoch in range(epochs):
        total_loss = []
        time1 = time.time()

        model.train()

        for step, (batch_x, batch_y) in enumerate(loader):
            optimizer.zero_grad()

            X = batch_x
            y = batch_y
            pred_y = model(X, avg)
            loss = crition(pred_y, y)
            total_loss.append(loss.data.cpu().numpy())
            loss.backward()
            optimizer.step()

        model.eval()
        val_X = val_data[:, :, :3]
        val_y = val_data[:, :, 3:5]
        # print(val_y.shape)
        pred_y = model(val_X, avg)
        pred_y = F.relu(pred_y)
        #print(pred_y.shape)
        val_loss = crition(pred_y, val_y)
        a = pred_y.data.cpu().numpy().reshape(1, -1)
        b = val_y.data.cpu().numpy().reshape(1, -1)

        train_loss = np.mean(total_loss)
        val_loss = val_loss.data.cpu().numpy().mean()
        print("Epoch", epoch,'train loss:', train_loss)
        print("Epoch", epoch,'validation loss:', val_loss )
        print(time.time() - time1)

        writer.add_scalars("scalar/loss", {'train_loss': train_loss}, epoch)
        writer.add_scalars("scalar/loss", {'val_loss': val_loss}, epoch)
        writer.add_scalars("scalar/loss", {'13.5': 13.5}, epoch)
        writer.add_scalars("scalar/loss", {'13.3': 13.3}, epoch)
        writer.add_scalars("scalar/loss", {'13.1': 13.1}, epoch)

        if epoch % 100 == 0:
            fpath = os.path.join(dataset_path, '28.npy')
            test_data = np.load(fpath)
            test_data = torch.from_numpy(test_data)
            test_data = torch.einsum("ijk->jik", test_data)
            ids = torch.from_numpy(np.array([i for i in range(81)])).double()
            ids = ids.repeat(144, 1)
            ids = ids.view(81, 144, 1)
            test_data = torch.cat([test_data, ids], dim=2).float().cuda(cuda_device)

            res = model(test_data, avg)
            res = F.relu(res)
            res = torch.einsum('ijk->jik', res)
            res = res.data.cpu().numpy()

            def time2str(id, date):
                dt = datetime.datetime.strptime(date, "%Y-%m-%d")
                t1 = time.mktime(dt.timetuple()) + int(id) * 10 * 60
                t2 = t1 + 10 * 60
                t1_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t1))
                t2_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t2))

                return t1_str, t2_str

            date = '2019-01-29'
            with open('./results/lstm_layers_dropout/{}-{}.csv'.format(date, epoch), 'w') as f:
                title = 'stationID,startTime,endTime,inNums,outNums'
                print(title, file=f)
                x, y, z = res.shape
                print(res[0][0])
                for j in range(y):
                    for i in range(x):
                        t1, t2 = time2str(i, date)
                        out_num, in_num = res[i][j]
                        in_num = '%.3f'%(in_num)
                        out_num = '%.3f'%(out_num)  
                        print(j, t1, t2, in_num, out_num, sep=',', file=f)


if __name__ == '__main__':
    train_glstm()
    #main()
