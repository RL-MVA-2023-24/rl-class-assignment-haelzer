from gymnasium.wrappers import TimeLimit
from env_hiv import HIVPatient
from copy import deepcopy
from tqdm import tqdm
import torch
import os
import random
import numpy as np
import pickle
import torch.nn as nn


env = TimeLimit(
    env=HIVPatient(domain_randomization=False), max_episode_steps=200
)  # The time wrapper limits the number of steps in an episode at 200.

# Now is the floor is yours to implement the agent and train it.


# You have to implement your own agent.
# Don't modify the methods names and signatures, but you can add methods.
# ENJOY!

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# Declare network
state_dim = env.observation_space.shape[0]
n_action = env.action_space.n 
nb_neurons= 50
DQN = torch.nn.Sequential(nn.Linear(state_dim, nb_neurons),
                            nn.SELU(),
                            nn.Linear(nb_neurons, 2*nb_neurons),
                            nn.SELU(), 
                            nn.Linear(2*nb_neurons, nb_neurons), 
                            # use diff activation function(empirically better than others)
                            nn.SELU(),
                            nn.Linear(nb_neurons, n_action)).to(device)
# DQN config
config = {'nb_actions': env.action_space.n,
        'learning_rate': 0.001,
        'gamma': 0.95,
        'buffer_size': 200000,
        'epsilon_min': 0.01,
        'epsilon_max': 1,
        'epsilon_decay_period': 20000,
        'epsilon_delay_decay': 20,
        'batch_size': 2000,
        'gradient_steps': 5,
        'update_target_strategy': 'ema', # or 'replace' or 'ema'
        'update_target_freq': 50,
        'update_target_tau': 0.0005,
        'criterion': torch.nn.SmoothL1Loss(),
        'monitoring_nb_trials': 0}

class ProjectAgent:
    def act(self, observation, use_random=False):
        # Implement your agent here  
        return self.agent.act(observation)

    def save(self, path):
        pass
        
    def load(self):
        self.agent = dqn_agent(config, DQN)
        path = os.getcwd() + "/src/model.pt"
        self.agent.model.load_state_dict(torch.load(path, 
                                                    map_location= torch.device('cpu')))
        
        
class ReplayBuffer:
    def __init__(self, capacity, device):
        self.capacity = capacity # capacity of the buffer
        self.data = []
        self.index = 0 # index of the next cell to be filled
        self.device = device
    def append(self, s, a, r, s_, d):
        if len(self.data) < self.capacity:
            self.data.append(None)
        self.data[self.index] = (s, a, r, s_, d)
        self.index = (self.index + 1) % self.capacity
    def sample(self, batch_size):
        batch = random.sample(self.data, batch_size)
        return list(map(lambda x:torch.Tensor(np.array(x)).to(self.device), list(zip(*batch))))
    def __len__(self):
        return len(self.data)
    
def greedy_action(network, state):
    device = "cuda" if next(network.parameters()).is_cuda else "cpu"
    with torch.no_grad():
        Q = network(torch.Tensor(state).unsqueeze(0).to(device))
        return torch.argmax(Q).item()
    

class dqn_agent:
    def __init__(self, config, model):
        device = "cuda" if next(model.parameters()).is_cuda else "cpu"
        self.nb_actions = config['nb_actions']
        self.gamma = config['gamma'] if 'gamma' in config.keys() else 0.95
        self.batch_size = config['batch_size'] if 'batch_size' in config.keys() else 100
        buffer_size = config['buffer_size'] if 'buffer_size' in config.keys() else int(1e5)
        self.memory = ReplayBuffer(buffer_size,device)
        self.epsilon_max = config['epsilon_max'] if 'epsilon_max' in config.keys() else 1.
        self.epsilon_min = config['epsilon_min'] if 'epsilon_min' in config.keys() else 0.01
        self.epsilon_stop = config['epsilon_decay_period'] if 'epsilon_decay_period' in config.keys() else 1000
        self.epsilon_delay = config['epsilon_delay_decay'] if 'epsilon_delay_decay' in config.keys() else 20
        self.epsilon_step = (self.epsilon_max-self.epsilon_min)/self.epsilon_stop
        self.model = model 
        self.target_model = deepcopy(self.model).to(device)
        self.criterion = config['criterion'] if 'criterion' in config.keys() else torch.nn.MSELoss()
        lr = config['learning_rate'] if 'learning_rate' in config.keys() else 0.001
        self.optimizer = config['optimizer'] if 'optimizer' in config.keys() else torch.optim.Adam(self.model.parameters(), lr=lr)
        self.nb_gradient_steps = config['gradient_steps'] if 'gradient_steps' in config.keys() else 1
        self.update_target_strategy = config['update_target_strategy'] if 'update_target_strategy' in config.keys() else 'replace'
        self.update_target_freq = config['update_target_freq'] if 'update_target_freq' in config.keys() else 20
        self.update_target_tau = config['update_target_tau'] if 'update_target_tau' in config.keys() else 0.005
        self.monitoring_nb_trials = config['monitoring_nb_trials'] if 'monitoring_nb_trials' in config.keys() else 0
        self.model_save_freq = config['save_model_freq'] if 'save_model_freq' in config.keys() else 50

    def MC_eval(self, env, nb_trials):   
        MC_total_reward = []
        MC_discounted_reward = []
        for _ in tqdm(range(nb_trials), "MC eval") :
            x,_ = env.reset()
            done = False
            trunc = False
            total_reward = 0
            discounted_reward = 0
            step = 0
            while not (done or trunc):
                a = greedy_action(self.model, x)
                y,r,done,trunc,_ = env.step(a)
                x = y
                total_reward += r
                discounted_reward += self.gamma**step * r
                step += 1
            MC_total_reward.append(total_reward)
            MC_discounted_reward.append(discounted_reward)
        return np.mean(MC_discounted_reward), np.mean(MC_total_reward)
    
    def V_initial_state(self, env, nb_trials):   # NEW NEW NEW
        with torch.no_grad():
            for _ in tqdm(range(nb_trials), "trials of V") :
                val = []
                x,_ = env.reset()
                val.append(self.model(torch.Tensor(x).unsqueeze(0).to(device)).max().item())
        return np.mean(val)
    
    def gradient_step(self):
        if len(self.memory) > self.batch_size:
            X, A, R, Y, D = self.memory.sample(self.batch_size)
            QYmax = self.target_model(Y).max(1)[0].detach()
            update = torch.addcmul(R, 1-D, QYmax, value=self.gamma)
            QXA = self.model(X).gather(1, A.to(torch.long).unsqueeze(1))
            loss = self.criterion(QXA, update.unsqueeze(1))
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step() 
    
    def train(self, env, max_episode):
        max_return = 0
        episode_return = []
        MC_avg_total_reward = []   
        MC_avg_discounted_reward = []   
        V_init_state = []  
        episode = 0
        episode_cum_reward = 0
        state, _ = env.reset()
        epsilon = self.epsilon_max
        step = 0
        while episode < max_episode:
            # update epsilon
            if step > self.epsilon_delay:
                epsilon = max(self.epsilon_min, epsilon-self.epsilon_step)
            # select epsilon-greedy action
            if np.random.rand() < epsilon:
                action = env.action_space.sample()
            else:
                action = greedy_action(self.model, state)
            # step
            next_state, reward, done, trunc, _ = env.step(action)
            self.memory.append(state, action, reward, next_state, done)
            episode_cum_reward += reward
            # train
            for _ in range(self.nb_gradient_steps): 
                self.gradient_step()
            # update target network if needed
            if self.update_target_strategy == 'replace':
                if step % self.update_target_freq == 0: 
                    self.target_model.load_state_dict(self.model.state_dict())
            if self.update_target_strategy == 'ema':
                target_state_dict = self.target_model.state_dict()
                model_state_dict = self.model.state_dict()
                tau = self.update_target_tau
                for key in model_state_dict:
                    target_state_dict[key] = tau*model_state_dict[key] + (1-tau)*target_state_dict[key]
                self.target_model.load_state_dict(target_state_dict)
            # next transition
            step += 1
            if done or trunc:
                episode += 1
                # Monitoring
                if self.monitoring_nb_trials>0:
                    MC_dr, MC_tr = self.MC_eval(env, self.monitoring_nb_trials)   
                    V0 = self.V_initial_state(env, self.monitoring_nb_trials)   
                    MC_avg_total_reward.append(MC_tr) 
                    MC_avg_discounted_reward.append(MC_dr)
                    V_init_state.append(V0) 
                    episode_return.append(episode_cum_reward)   
                    print("Episode ", '{:2d}'.format(episode), 
                          ", epsilon ", '{:6.2f}'.format(epsilon), 
                          ", batch size ", '{:4d}'.format(len(self.memory)), 
                          ", ep return ", '{:4.1f}'.format(episode_cum_reward), 
                          ", MC tot ", '{:6.2f}'.format(MC_tr),
                          ", MC disc ", '{:6.2f}'.format(MC_dr),
                          ", V0 ", '{:6.2f}'.format(V0),
                          sep='')
                else:
                    episode_return.append(episode_cum_reward)
                    print("Episode ", '{:2d}'.format(episode), 
                          ", epsilon ", '{:6.2f}'.format(epsilon), 
                          ", batch size ", '{:4d}'.format(len(self.memory)), 
                          ", ep return ", '{:e}'.format(episode_cum_reward), 
                          sep='')  
                state, _ = env.reset()
                episode_cum_reward = 0
            else:
                state = next_state
        return episode_return, MC_avg_discounted_reward, MC_avg_total_reward, V_init_state
    
    def random_fill(self, samples, env) :
        state, _ = env.reset()
        for i in tqdm(range(samples)):
            action = env.action_space.sample()
            next_state, reward, done, trunc, _ = env.step(action)
            self.memory.append(state, action, reward, next_state, done)
            if done or trunc :
                state, _ = env.reset()
            else :
                state = next_state
    def greedy_fill(self, samples, env):
        state, _ = env.reset()
        for i in tqdm(range(samples)):
            action = greedy_action(self.model, state)
            next_state, reward, done, trunc, _ = env.step(action)
            self.memory.append(state, action, reward, next_state, done)
            if done or trunc :
                state, _ = env.reset()
            else :
                state = next_state
            
    def save(self, episode):
        print("Saving model, target model and optimizer ")
        #torch.save(self.model.state_dict(), 'models/model_{:e}'.format(episode))
        #torch.save(self.target_model.state_dict(), 'targets/target_model_{:e}'.format(episode))
        #torch.save(self.optimizer.state_dict(), 'optimizers/optimizer_model_{:e}'.format(episode))
        #with open('agents/agent_{:e}.pkl'.format(episode), 'wb') as f:
            #pickle.dump(self, f)
        torch.save(self.model.state_dict(), 'model.pt')

    def load(self, model_path, target_model_path, optimizer_path):
        self.model.load_state_dict(torch.load(model_path))
        self.target_model.load_state_dict(torch.load(target_model_path))
        self.optimizer.load_state_dict(torch.load(optimizer_path))
    
    def act(self, state):
        return greedy_action(self.model, state)
    
if __name__ == "__main__":
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # Declare network
    state_dim = env.observation_space.shape[0]
    n_action = env.action_space.n 
    nb_neurons= 50
    DQN = torch.nn.Sequential(nn.Linear(state_dim, nb_neurons),
                            nn.SELU(),
                            nn.Linear(nb_neurons, 2*nb_neurons),
                            nn.SELU(), 
                            nn.Linear(2*nb_neurons, nb_neurons), 
                            # use diff activation function(empirically better than others)
                            nn.SELU(),
                            nn.Linear(nb_neurons, n_action)).to(device)
    # DQN
    config = {'nb_actions': env.action_space.n,
            'learning_rate': 0.001,
            'gamma': 0.95,
            'buffer_size': 500000,
            'epsilon_min': 0.01,
            'epsilon_max': 1,
            'epsilon_decay_period': 20000,
            'epsilon_delay_decay': 20,
            'batch_size': 2000,
            'gradient_steps': 5,
            'update_target_strategy': 'ema', # or 'replace' or 'ema'
            'update_target_freq': 50,
            'update_target_tau': 0.0005,
            'criterion': torch.nn.SmoothL1Loss(),
            'monitoring_nb_trials': 0}
    # Declare agent
    agent = dqn_agent(config, DQN)
    load = False
    if load:
        # load agent using pickle
        with open('./src/agents/agent.pkl', 'rb') as f:
            agent = pickle.load(f)
   
    # Train agent
    #max_episode_steps=2200
    #ep_length, disc_rewards, tot_rewards, V0 = agent.train(env, max_episode_steps)
    #with open('./src/agents/agent.pkl', 'wb') as f:
        #pickle.dump(agent, f)
