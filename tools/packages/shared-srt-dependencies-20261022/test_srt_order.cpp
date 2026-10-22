// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <algorithm>
#include <cstdint>
#include <functional>
#include <iostream>
#include <list>
#include <stdexcept>
#include <vector>
#include <boost/container/flat_map.hpp>
namespace IR {
struct Block;struct Node;
struct Value {
 bool literal{};std::uint32_t number{};Node* node{};
 bool IsImmediate()const{return literal;}std::uint32_t U32()const{return number;}
 Node* Inst()const{return node;}
 bool operator==(const Value&)const=default;
};
struct Node {Block* parent{};Block* GetParent()const{return parent;}};
struct Nodes:std::list<Node>{auto iterator_to(Node&n){return std::find_if(begin(),end(),[&](auto&i){return &i==&n;});}};
struct Block {Nodes nodes;Nodes& Instructions(){return nodes;}};
}
#include "comparator_under_test.inc"
static void check(bool b,const char* s){if(!b)throw std::runtime_error(s);}
int main(){try{
 IR::Block a,b;std::vector<IR::Value> values{{true,0,nullptr},{true,7,nullptr}};
 for(auto*p:{&a,&b})for(int i=0;i<4;++i){p->nodes.push_back({p});values.push_back({false,0,&p->nodes.back()});}
 PointerListCompare cmp;size_t triples=0;
 for(auto x:values){check(!cmp(x,x),"comparator self-order is true");for(auto y:values){check(!(cmp(x,y)&&cmp(y,x)),"asymmetry");
  for(auto z:values){if(cmp(x,y)&&cmp(y,z))check(cmp(x,z),"transitivity");++triples;}}}
 boost::container::flat_map<IR::Value,int,PointerListCompare> map;
 for(auto x:values){map.insert_or_assign(x,1);map.insert_or_assign(x,2);check(map.at(x)==2,"key lookup");}
 check(map.size()==values.size(),"duplicate keys");
 std::cout<<"SRT_SOURCE_ORDER=PASS triples="<<triples<<" actual_boost_flat_map=YES\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 2;}}
