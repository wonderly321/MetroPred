# Author: h12345jack
import os
import time
import datetime


import numpy as np
import torch
import torch.nn as nn

import torch.utils.data as Data
import torch.nn.functional as F

from torch_geometric.nn import GCNConv, SGConv
from torch_geometric.data import Data as geoData

from sklearn.metrics import mean_absolute_error


cuda_device = torch.device('cuda:3')

LEARNING_RATE = 0.01
WEIGHT_DECAY = 0.0001
epochs = 500
batch_size = 50
torch.manual_seed(13)
CUDA = True

#
class BiLSTM(nn.Module):
    def __init__(self, dim=100, dim1=20, dim2=20):
        super(BiLSTM, self).__init__()
        bidirectional = True
        linear_dim = dim * 2 if bidirectional else dim
        self.rnn = nn.LSTM(dim1+dim2, dim,
                           num_layers = 2,
                           batch_first=True,
                           bidirectional=bidirectional,
                           dropout=0.2
                  )
        self.none_linear_layer = nn.Sequential(
            nn.Linear(linear_dim, 50),
            nn.ReLU(),
            nn.Linear(50, 2),
            nn.ReLU()
        )

    def forward(self, data):
        result, _ = self.rnn(data)
        result = self.none_linear_layer(result)
        return result

class GCNNet(nn.Module):
    def __init__(self, node_num):
        super(GCNNet, self).__init__()
        dim = 20
        self.node_embedding = nn.Embedding(node_num, dim)
        self.node_embedding.weight.requires_grad = True
        self.node_embedding.cuda()

        self.conv1 = GCNConv(dim, dim)
        self.conv2 = GCNConv(dim, dim)

    def forward(self, data):
        x, edge_index, edge_weight = data.x, data.edge_index, data.edge_attr
        x = x.long()
        edge_index = edge_index.long()
        x = self.node_embedding(x)

        # print(self.node_embedding(torch.Tensor([0]).long().cuda()), 70)

        x = self.conv1(x, edge_index)
        x = F.relu(x)
        x = F.dropout(x, training=self.training)
        x = self.conv2(x, edge_index)
        return x


class Model(nn.Module):
    def __init__(self, graph_data):
        super(Model, self).__init__()
        self.lstm = BiLSTM()
        self.gcn = GCNNet(81)
        self.graph_data = graph_data
        self.fc = nn.Sequential(
            nn.Linear(2, 20)
        )

    def forward(self, data):
        # data bs * 144, 3

        network_emb = self.gcn(self.graph_data)

        assert  network_emb.shape == (81, 20)
        x = data[:, :, :2]
        new_x1 = self.fc(x)
        ids = data[:, :, 2].long()
        network_emb1 = nn.Embedding.from_pretrained(network_emb, freeze=False)

        # print(network_emb1(torch.Tensor([0]).long().cuda()), 162)


        new_x2 = network_emb1(ids)
        x = torch.cat([new_x1, new_x2], dim=2)
        res = self.lstm(x)

        return res



def read_graph_csv(fpath='./maps/graph.csv'):
    datas = []
    with open(fpath) as f:
        for line in f.readlines():
            ll = line.split(",")
            datas.append([float(i) for i in ll])
    graph = np.array(datas)
    assert graph.shape == (81, 81)
    return graph


def main():
    dataset_path = './dataset/h_train'

    val = [24, 25]

    datas = []

    noises = [1, 5, 6, 12, 13, 19, 20]

    for i in range(25):
        fpath = os.path.join(dataset_path, '{}.npy'.format(i + 1))
        data = np.load(fpath)
        #  144 * 81 * 2
        data = torch.from_numpy(data)
        data = torch.einsum("ijk->jik", data)
        # 81 * 144 * 2
        # 浪费内存吧，反正内存不值钱
        ids = torch.from_numpy(np.array([np.ones(144)*i for i in range(81)])).double()
        # ids = ids.repeat(144, 1)
        ids = ids.view(81, 144, 1)
        data = torch.cat([data, ids], dim=2)
        assert data.shape == (81, 144, 3)
        datas.append(data)

    X = []
    for i in range(24 - 1):
        if i + 1 in noises: continue
        if i + 2 in noises: continue
        a = datas[i]
        b = datas[i + 1]
        c = torch.cat((a, b), dim=2)
        X.append(c)


    graph = read_graph_csv()
    rows, cols = graph.nonzero()
    rows = rows.reshape(-1)
    cols = cols.reshape(-1)
    edge_index = torch.tensor([rows, cols], dtype=torch.float).cuda()
    edge_weight = torch.from_numpy(graph[rows, cols]).float().cuda()


    x = torch.tensor([i for i in range(81)], dtype=torch.float).cuda()

    graph_data = geoData(x, edge_index=edge_index, edge_attr=edge_weight)

    all_data = torch.cat(X, dim=0).float().cuda()

    a = datas[val[0] - 1]
    b = datas[val[1] - 1]
    val_data = torch.cat((a, b), dim=2).float().cuda()


    torch_dataset = Data.TensorDataset(all_data[:,:,:3], all_data[:,:, 3:5])
    loader = Data.DataLoader(
        dataset = torch_dataset,
        batch_size = batch_size,
        shuffle = True,
        num_workers= 0
    )
    model = Model(graph_data)
    model.cuda()

    crition = nn.L1Loss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)

    for epoch in range(epochs):
        total_loss = []
        time1 = time.time()

        model.train()

        for step, (batch_x, batch_y) in enumerate(loader):
            optimizer.zero_grad()

            X = batch_x
            y = batch_y[:, :, :]
            pred_y = model(X)
            loss = crition(pred_y, y)
            total_loss.append(loss.data.cpu().numpy())
            loss.backward()
            optimizer.step()

        model.eval()
        val_X = val_data[:, :, :3]
        val_y = val_data[:, :, 3:5]
        # print(val_y.shape)
        pred_y = model(val_X)
        # print(pred_y.shape, val_y.shape)
        val_loss = crition(pred_y, val_y)
        a = pred_y.data.cpu().numpy().reshape(1, -1)
        b = val_y.data.cpu().numpy().reshape(1, -1)
        val_loss_sklearn = mean_absolute_error(a, b)
        print("Epoch", epoch, 'train loss:', np.mean(total_loss))
        print("Epoch", epoch, 'validation loss:', val_loss.data.cpu().numpy().mean() )
        print(time.time() - time1)

        # if epoch % 10 == 0:
        #     fpath = os.path.join(dataset_path, '28.npy')
        #     test_data = np.load(fpath)
        #
        #     # data = torch.from_numpy(data)
        #     # data = torch.einsum("ijk->jik", data)
        #     # # 81 * 144 * 2
        #     # # 浪费内存吧，反正内存不值钱
        #     # ids = torch.from_numpy(np.array([i for i in range(81)])).double()
        #     # ids = ids.repeat(144, 1)
        #     # ids = ids.view(81, 144, 1)
        #     # data = torch.cat([data, ids], dim=2)
        #
        #
        #     test_data = torch.from_numpy(test_data)
        #     test_data = torch.einsum("ijk->jik", test_data)
        #     ids = torch.from_numpy(np.array([i for i in range(81)])).double()
        #     ids = ids.repeat(144, 1)
        #     ids = ids.view(81, 144, 1)
        #     test_data = torch.cat([test_data, ids], dim=2).float().cuda()
        #
        #     res = model(test_data)
        #     res = torch.einsum('ijk->jik', res)
        #     res = res.data.cpu().numpy()
        #
        #     def time2str(id, date):
        #         dt = datetime.datetime.strptime(date, "%Y-%m-%d")
        #         t1 = time.mktime(dt.timetuple()) + int(id) * 10 * 60
        #         t2 = t1 + 10 * 60
        #         t1_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t1))
        #         t2_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t2))
        #
        #         return t1_str, t2_str
        #
        #     date = '2019-01-29'
        #     with open('./hjj_results_gcn/{}-{}.csv'.format(date, epoch), 'w') as f:
        #         title = 'stationID,startTime,endTime,inNums,outNums'
        #         print(title, file=f)
        #         x, y, z = res.shape
        #         print(res[0][0])
        #         for j in range(y):
        #             for i in range(x):
        #                 t1, t2 = time2str(i, date)
        #                 out_num, in_num = res[i][j]  # 0出，1进
        #                 print(j, t1, t2, in_num, out_num, sep=',', file=f)


if __name__ == '__main__':
    main()