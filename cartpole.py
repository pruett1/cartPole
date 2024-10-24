import gymnasium as gym
from gymnasium import wrappers
import math
import random
import matplotlib
import matplotlib.pyplot as plt
from collections import namedtuple, deque
from itertools import count

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

from time import sleep

import faulthandler
faulthandler.enable()

class CustomReward(gym.Wrapper):
    def __init__(self, env):
        super(CustomReward, self).__init__(env)
    
    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)

        reward = 1 * (1/(1-abs(obs[0]))) - abs(obs[2]) # smaller reward the more the pole deviates from vertical or center

        return obs, reward, terminated, truncated, info

env = CustomReward(gym.make("CartPole-v1", render_mode="human"))

is_ipython = 'inline' in matplotlib.get_backend()
if is_ipython:
    from IPython import display

plt.ion()

device = torch.device("cpu")

Transition = namedtuple('Transition', ('state', 'action', 'next_state', 'reward'))

class ReplayMemory(object):
    def __init__(self, capacity):
        self.memory = deque([], maxlen=capacity)
    
    def push(self, *args):
        """Save a transition"""
        self.memory.append(Transition(*args))

    def sample(self, batch_size):
        return random.sample(self.memory, batch_size)
    
    def __len__(self):
        return len(self.memory)

class DQN(nn.Module):
    def __init__(self, n_observations, n_actions):
        super(DQN, self).__init__()
        self.layer1 = nn.Linear(n_observations, 128)
        self.layer2 = nn.Linear(128, 128)
        self.layer3 = nn.Linear(128, 128)
        self.layer4 = nn.Linear(128, n_actions)

    # Called with either one element to determine next adtions, or a batch during optimization
    # Returns tensor([[left0exp, right0exp], ...])
    def forward(self, x):
        x = F.relu(self.layer1(x))
        x = F.relu(self.layer2(x))
        x = F.relu(self.layer3(x))
        return self.layer4(x)

# HYPERPARAMETERS
# batch_size = number of transitions sampled from replay buffer
# gamma = discount factor for the DQN algo to ensure sum converges
# eps_start = starting epsilon val (espilon val is prob of choosing random action)
# eps_end = ending epsilon val
# eps_decay = controls the rate at which epsilon will exponentially decay (inverse to rate of decay)
# tau = update rate of network
# lr = learning rate of AdamW optimizer
# early_stopping_threshold = number of consecutive episodes where the model truncates before it is stopped training
BATCH_SIZE = 128
GAMMA = 0.8
EPS_START = 0.9
EPS_END = 0.05
EPS_DECAY = 1000
TAU = 0.005
LR = 1e-4
EARLY_STOPPING_THRESHOLD = 10

# get number of actions from action space
n_actions = env.action_space.n

# get number of state observations
state, info = env.reset()
n_observations = len(state)

policy_net = DQN(n_observations, n_actions).to(device)
target_net = DQN(n_observations, n_actions).to(device)
target_net.load_state_dict(policy_net.state_dict())

optimizer = optim.AdamW(policy_net.parameters(), lr=LR, amsgrad=True)
memory = ReplayMemory(10000)

steps_done = 0

def select_action(state):
    global steps_done
    sample = random.random()
    eps_threshold = EPS_END + (EPS_START - EPS_END) * math.exp(-1 * steps_done / EPS_DECAY)
    steps_done += 1
    if sample > eps_threshold: 
        with torch.no_grad():
            # t.max(1) will return the largest column value of each row.
            # second column on max result is index of where max element was
            # found, so we pick action with the larger expected reward.
            return policy_net(state).max(1).indices.view(1, 1)
    else: 
        return torch.tensor([[env.action_space.sample()]], device=device, dtype=torch.long)

episode_durations = []

def plot_durations(show_result = False):
    plt.figure(1)
    durations_t = torch.tensor(episode_durations, dtype=torch.float)
    if show_result:
        plt.title('Result')
    else:
        plt.clf()
        plt.title('Training...')
    plt.xlabel('Episode')
    plt.ylabel('Duration')
    plt.plot(durations_t.numpy())
    
    # take 100 episode averages and plot them out
    if len(durations_t) >= 100:
        means = durations_t.unfold(0, 100, 1).mean(1).view(-1)
        means = torch.cat((torch.zeros(99), means))
        plt.plot(means.numpy())
    
    plt.pause(0.001) #pause to update plots
    if is_ipython: 
        if not show_result:
            display.display(plt.gcf())
            display.clear_output(wait=True)
        else:
            display.display(plt.gcf())

def optimize_model():
    if len(memory) < BATCH_SIZE:
        return
    transitions = memory.sample(BATCH_SIZE)
    # Transpose the batch. Converts batch-array of Transitions to Transition of batch-array
    batch = Transition(*zip(*transitions))

    # Compute mask of non-final states and concat batch elems
    # Final state would be the one after sim was ended
    non_final_mask = torch.tensor(tuple(map(lambda s: s is not None, batch.next_state)), device=device, dtype=torch.bool)
    non_final_next_states = torch.cat([s for s in batch.next_state if s is not None])

    state_batch = torch.cat(batch.state)
    action_batch = torch.cat(batch.action)
    reward_batch = torch.cat(batch.reward)

    # Compute Q(s_t, a) - model computes Q(s_t) then select the columns of actions taken
    # These are the actions which would've been taken for each batch state according to policy_net
    state_action_values = policy_net(state_batch).gather(1, action_batch)

    # Compute V(s_{t+1}) for all next states
    # Expected values of actions for non_final_next_states are computed based on the "older" target_net;
    # selecting their best reward with max(1).values
    # This merged based on the mask, such that well have either the expected state value or 0 if it is the final state
    next_state_values = torch.zeros(BATCH_SIZE, device=device)
    with torch.no_grad():
        next_state_values[non_final_mask] = target_net(non_final_next_states).max(1).values

    # Compute expected Q values
    expected_state_action_values = (next_state_values * GAMMA) + reward_batch

    # Compute Huber Loss
    criterion = nn.SmoothL1Loss()
    loss = criterion(state_action_values, expected_state_action_values.unsqueeze(1))

    # Optimize model
    optimizer.zero_grad()
    loss.backward()
    # In-place gradient clipping
    torch.nn.utils.clip_grad_value_(policy_net.parameters(), 100)
    optimizer.step()

# Main training loop
if torch.cuda.is_available() or torch.backends.mps.is_available():
    num_episodes = 600
else: 
    num_episodes = 600

trunc_count = 0
prev_trunc_ep = 0

for i_episode in range(num_episodes):
    # Initialize env and get state
    state, info = env.reset()
    state = torch.tensor(state, dtype=torch.float32, device=device).unsqueeze(0)

    for t in count():
        action = select_action(state)
        observation, reward, terminated, truncated, _ = env.step(action.item())
        reward = torch.tensor([reward], device=device)
        done = terminated or truncated

        if terminated:
            next_state = None
        else: 
            next_state = torch.tensor(observation, dtype=torch.float32, device=device).unsqueeze(0)
        
        # Store the transition in memory
        memory.push(state, action, next_state, reward)

        # Move to the next state
        state = next_state

        # Preform one step of optimization on the policy net
        optimize_model()

        # Soft update of the target networks weights
        # θ' <- τ θ + (1-τ)θ'
        target_net_state_dict = target_net.state_dict()
        policy_net_state_dict = policy_net.state_dict()

        for key in policy_net_state_dict:
            target_net_state_dict[key] = policy_net_state_dict[key]*TAU+target_net_state_dict[key]*(1-TAU)
        
        target_net.load_state_dict(target_net_state_dict)

        if done:
            episode_durations.append(t+1)
            plot_durations()
            break

    if truncated: 
            if trunc_count == 0 or i_episode - 1 == prev_trunc_ep:
                trunc_count += 1
                prev_trunc_ep = i_episode
            else:
                trunc_count = 1
                prev_trunc_ep = i_episode
    
    if trunc_count > EARLY_STOPPING_THRESHOLD:
        print('Early stopping threshold met...')
        break
    
print('Complete')
plot_durations(show_result=True)
plt.ioff()
plt.show()

sleep(5)

def watch_trained_model(env, policy_net, num_episodes=5):
    for i_episode in range(num_episodes):
        state, _ = env.reset()
        state = torch.tensor(state, dtype=torch.float32, device=device).unsqueeze(0)
        
        for t in count():
            env.render()  # Render the environment
            
            # Select the best action using the trained policy network (no exploration)
            with torch.no_grad():
                action = policy_net(state).max(1).indices.view(1, 1)
            
            # Take the action in the environment
            observation, reward, terminated, truncated, _ = env.step(action.item())
            state = torch.tensor(observation, dtype=torch.float32, device=device).unsqueeze(0)
            
            # If episode ends, break the loop
            if terminated or truncated:
                print(f"Episode {i_episode+1} finished after {t+1} timesteps")
                break

    env.close()  # Close the environment after running

print("Training complete, now watching the trained model...")
watch_trained_model(env, policy_net)