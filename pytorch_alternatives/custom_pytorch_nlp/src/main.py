"""CNN-based text classification on SageMaker with Pytorch"""

# Python Built-Ins:
import argparse
import os
import io
import logging
import sys

# External Dependencies:
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import DataLoader

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler(sys.stdout))

###### Helper functions ############
class Dataset(torch.utils.data.Dataset):
    def __init__(self, data, labels):
        'Initialization'
        self.labels = labels
        self.data = data

    def __len__(self):
        'Denotes the total number of samples'
        return len(self.data)

    def __getitem__(self, index):
        # Load data and get label
        X = torch.as_tensor(self.data[index]).long()
        y = torch.as_tensor(self.labels[index])
        return X, y

def load_training_data(base_dir):
    X_train = np.load(os.path.join(base_dir, "train_X.npy"))
    y_train = np.load(os.path.join(base_dir, "train_Y.npy"))
    return DataLoader(Dataset(X_train, y_train), batch_size=16)

def load_testing_data(base_dir):
    X_test = np.load(os.path.join(base_dir, "test_X.npy"))
    y_test = np.load(os.path.join(base_dir, "test_Y.npy"))
    return DataLoader(Dataset(X_test, y_test), batch_size=1)

def load_embeddings(base_dir):
    embedding_matrix = np.load(os.path.join(base_dir, "docs-embedding-matrix.npy"))
    return embedding_matrix

def parse_args():
    """Acquire hyperparameters and directory locations passed by SageMaker"""
    parser = argparse.ArgumentParser()

    # Hyperparameters sent by the client are passed as command-line arguments to the script.
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--learning_rate", type=float, default=0.001)
    parser.add_argument("--num_classes", type=int, default=4)
    parser.add_argument("--vocab_size", type=int, default=400000)

    # Data, model, and output directories
    parser.add_argument("--output-data-dir", type=str, default=os.environ["SM_OUTPUT_DATA_DIR"])
    parser.add_argument("--model-dir", type=str, default=os.environ["SM_MODEL_DIR"])
    parser.add_argument("--train", type=str, default=os.environ["SM_CHANNEL_TRAIN"])
    parser.add_argument("--test", type=str, default=os.environ["SM_CHANNEL_TEST"])
    parser.add_argument("--embeddings", type=str, default=os.environ["SM_CHANNEL_EMBEDDINGS"])

    return parser.parse_known_args()

class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.embedding = nn.Embedding(400000, 100)
        self.conv1 = nn.Conv1d(100, 128, kernel_size=3)
        self.max_pool1d = nn.MaxPool1d(5)
        self.flatten1 = nn.Flatten()
        self.dropout1 = nn.Dropout(p=0.3)
        self.fc1 = nn.Linear(896, 128)
        self.fc2 = nn.Linear(128, 4)

    def forward(self, x):
        x = self.embedding(x)
        x = torch.transpose(x,1,2)
        x = self.flatten1(self.max_pool1d(self.conv1(x)))
        x = self.dropout1(x)
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return F.softmax(x)

def test(model, test_loader, device):
    model.eval()
    test_loss = 0
    #correct = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            test_loss += F.binary_cross_entropy(output, target, size_average=False).item()  # sum up batch loss
            pred = output.max(1, keepdim=True)[1]  # get the index of the max log-probability
            #correct += pred.eq(target.view_as(pred)).sum().item()

    test_loss /= len(test_loader.dataset)
    print("val_loss: {:.4f}".format(test_loss))
    #print("val_acc:{:.4f}".format(correct/len(test_loader.dataset)))   

def train(args):
    ###### Load data from input channels ############
    train_loader = load_training_data(args.train)
    test_loader = load_testing_data(args.test)
    embedding_matrix = load_embeddings(args.embeddings)

    ###### Setup model architecture ############
    model = Net()
    model.embedding.weight = torch.nn.parameter.Parameter(torch.FloatTensor(embedding_matrix), False)

    device = torch.device('cpu')
    if torch.cuda.is_available():
        device = torch.device('cuda')
    model.to(device)
    model = torch.nn.DataParallel(model)
    optimizer = optim.RMSprop(model.parameters(), lr=args.learning_rate)

    for epoch in range(1, args.epochs + 1):
        model.train()
        for batch_idx, (X_train, y_train) in enumerate(train_loader, 1):
            data, target = X_train.to(device), y_train.to(device)
            optimizer.zero_grad()
            output = model(data)
            loss = F.binary_cross_entropy(output, target)
            loss.backward()
            #if is_distributed and not use_cuda:
                # average gradients manually for multi-machine cpu case only
            #_average_gradients(model)
            optimizer.step()
            if (len(train_loader.dataset) - batch_idx*16) <= 16:
                print("epoch: {}".format(epoch))
                print("train_loss: {:.6f}".format(loss.item()))
        print("Evaluating model")
        test(model, test_loader, device)
    save_model(model, args.model_dir)

def save_model(model, model_dir):
    path = os.path.join(model_dir, 'model.pt')
    m = torch.jit.script(Net())
    m.save(path)
    
    # recommended way from http://pytorch.org/docs/master/notes/serialization.html
    # path = os.path.join(model_dir, 'model.pth')
    # torch.save(model.cpu().state_dict(), path)
    
'''
def model_fn(model_dir):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = torch.nn.DataParallel(Net())
    model.eval()
    with open(os.path.join(model_dir, 'model.pth'), 'rb') as f:
        model.load_state_dict(torch.load(f))
    return model.to(device)
'''

###### Main application  ############
if __name__ == "__main__":

    ###### Parse input arguments ############
    args, unknown = parse_args()

    train(args)
