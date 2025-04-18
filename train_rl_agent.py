import torch
import numpy as np
import wandb
from sac_agent import SACAgent
from kv_cache_env import KVCacheEnv
from llama import LLAMA
from visualization import KVCacheVisualizer
import argparse
import json
import random

def load_training_data(data_path):
    with open(data_path, 'r') as f:
        return json.load(f)

def generate_prompt(training_data):
    sample = random.choice(training_data)
    return sample['prompt']

def train(args):
    wandb.init(project="adaptive-kv-cache", config=args)
    visualizer = KVCacheVisualizer(wandb.run.name)
    
    training_data = load_training_data(args.training_data_path)
    
    llama_model = LLAMA(
        vocab_size=args.vocab_size,
        embed_dim=args.embed_dim,
        max_seq_len=args.max_seq_len,
        n_layers=args.n_layers,
        n_heads=args.n_heads,
        forward_mul=args.forward_mul,
        kv_cache=True
    )
    
    env = KVCacheEnv(
        llama_model=llama_model,
        max_primary_size=args.max_primary_size,
        max_secondary_size=args.max_secondary_size,
        lambda_cost=args.lambda_cost,
        semantic_weight=args.semantic_weight,
        cache_miss_penalty=args.cache_miss_penalty,
        perplexity_weight=args.perplexity_weight,
        attention_weight=args.attention_weight,
        gradient_weight=args.gradient_weight
    )
    
    agent = SACAgent(
        state_dim=env.get_state_space(),
        action_dim=env.get_action_space(),
        hidden_dim=args.hidden_dim,
        lr=args.lr,
        gamma=args.gamma,
        tau=args.tau,
        alpha=args.alpha
    )
    
    state_buffer = []
    action_buffer = []
    reward_buffer = []
    next_state_buffer = []
    done_buffer = []
    
    for episode in range(args.num_episodes):
        prompt = generate_prompt(training_data)
        state = env.reset(prompt=prompt)
        episode_reward = 0
        episode_metrics = {}
        
        for step in range(args.max_steps):
            action = agent.select_action(state)
            next_state, reward, done, info = env.step(action)
            
            state_buffer.append(state)
            action_buffer.append(action)
            reward_buffer.append(reward)
            next_state_buffer.append(next_state)
            done_buffer.append(done)
            
            episode_reward += reward
            episode_metrics.update(info)
            
            if len(state_buffer) >= args.batch_size:
                agent.update(
                    np.array(state_buffer),
                    np.array(action_buffer),
                    np.array(reward_buffer),
                    np.array(next_state_buffer),
                    np.array(done_buffer)
                )
                
                state_buffer = []
                action_buffer = []
                reward_buffer = []
                next_state_buffer = []
                done_buffer = []
                
            if done:
                break
                
            state = next_state
            
        visualizer.update_metrics(episode_metrics)
        visualizer.plot_training_progress(episode)
        visualizer.plot_reward_components(episode)
        visualizer.plot_cache_state(env.storage.primary_cache, env.storage.secondary_cache)
        visualizer.log_summary_statistics(episode)
        
        wandb.log({
            'episode': episode,
            'episode_reward': episode_reward,
            'steps': step + 1,
            'prompt': prompt
        })
        
        if (episode + 1) % args.save_interval == 0:
            torch.save({
                'actor_state_dict': agent.actor.state_dict(),
                'critic1_state_dict': agent.critic1.state_dict(),
                'critic2_state_dict': agent.critic2.state_dict(),
                'actor_optimizer_state_dict': agent.actor_optimizer.state_dict(),
                'critic1_optimizer_state_dict': agent.critic1_optimizer.state_dict(),
                'critic2_optimizer_state_dict': agent.critic2_optimizer.state_dict()
            }, f'saved_models/sac_agent_episode_{episode+1}.pt')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    
    parser.add_argument('--vocab_size', type=int, default=32000)
    parser.add_argument('--embed_dim', type=int, default=512)
    parser.add_argument('--max_seq_len', type=int, default=2048)
    parser.add_argument('--n_layers', type=int, default=6)
    parser.add_argument('--n_heads', type=int, default=8)
    parser.add_argument('--forward_mul', type=int, default=4)
    
    parser.add_argument('--max_primary_size', type=int, default=1024)
    parser.add_argument('--max_secondary_size', type=int, default=2048)
    parser.add_argument('--lambda_cost', type=float, default=0.1)
    parser.add_argument('--semantic_weight', type=float, default=0.3)
    parser.add_argument('--cache_miss_penalty', type=float, default=0.5)
    parser.add_argument('--perplexity_weight', type=float, default=0.2)
    parser.add_argument('--attention_weight', type=float, default=0.3)
    parser.add_argument('--gradient_weight', type=float, default=0.2)
    
    parser.add_argument('--hidden_dim', type=int, default=256)
    parser.add_argument('--lr', type=float, default=3e-4)
    parser.add_argument('--gamma', type=float, default=0.99)
    parser.add_argument('--tau', type=float, default=0.005)
    parser.add_argument('--alpha', type=float, default=0.2)
    
    parser.add_argument('--num_episodes', type=int, default=1000)
    parser.add_argument('--max_steps', type=int, default=1000)
    parser.add_argument('--batch_size', type=int, default=64)
    parser.add_argument('--save_interval', type=int, default=100)
    parser.add_argument('--training_data_path', type=str, required=True, help='Path to JSON file containing training prompts')
    
    args = parser.parse_args()
    train(args) 