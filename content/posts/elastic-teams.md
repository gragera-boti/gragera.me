---
title: "Elastic teams"
date: "2020-05-05T22:27:57+0000"
draft: false
tags:
  - "teams"
featured_image: "/content/images/2026/03/generated_image2.jpg"
---

I worked at a [pretty big social network](<https://en.wikipedia.org/wiki/Tuenti>) in Spain from 2009 to 2013. Tuenti peaked at roughly 16 million users and 25 billion page views a month, which was pretty insane back in the day. We made it to the Google Zeitgeist for a couple of years, and in 2011 it was estimated that 15% of the internet traffic of Spain was through Tuenti, more than Google and Facebook combined.

There was no Google Cloud \(the app engine was launched in preview in 2008, but it was nothing remotely production ready\) nor AWS. 

Everything ran in bare metal. 

Some days \(usually after some weekend/holiday\) we needed to configure frontend servers as image uploaders and image processors to be able to cope with the load. This was a manual process that was done on-demand, not a lot of elasticity at the time.

* * *

Nowadays the cloud is really, really convenient. Having the ability to spin up more computing instances when needed, with predefined docker images that will do the task you want them to do is amazing and has transformed the way we build digital products. It provides **flexibility**. It has made the market much more accessible \(no need to buy or lease actual machines from your won pocket\) and it is easier and much cheaper to try things out and see if they have any traction.

### Engineering teams: Specialist vs Generalist

When building engineering teams, **I want the same flexibility**. I want to have engineers that are able to do many things, depending on what needs to be done at a given point in time. Sometimes you will need to push more in the apps, some other times in some backend services and some other time it will be the frontend. 

It is very hard to anticipate the needs when building the teams. Every project is different, each part of the system moves at a different pace and the roadmaps are not written in stone. What your team planned to do in Q4 while being in Q2 tends to be different to what your are really going to do in Q4. We must embrace this as the reality.

While having deep knowledge about a particular technology is very valuable \(depending on the problem at hand\), I am a firm believer of horizontal knowledge, specially when working at a startup. The ability to jump between codebases and problem spaces without worrying too much about the underlying technology is a blessing.

And the wheel keeps spinning: the more you do this, the broader your knowledge of the system is, and the more you \(or your team\) can impact, as you will be able to go faster.

Elastic teams \(slightly\) mitigate one of the most common problems of companies at pretty much any scale, **cross dependencies**. Rather than asking whatever team to do something, you can get to an agreement on what needs to be done \(this is really the biggest problem in the industry, **communication**\) to solve your specific use case, and then execute on your own. 

### Manager / tech lead

A manager/tech lead with a broader knowledge about different parts of the system and technologies \(mobile and backend, for instance\) is incredibly valuable as the lead will be able to empathize with the team, understand pain points, anticipate problems and provide much better insights.

I am not telling that you should be a top iOS developer and a super specialist in building scalable distributed systems, but you should try to know your way around that, even at a basic level. 

### Hiring

This is one of the trickiest parts. It is very hard to find people with this set of skills right off the bat, so you are better off hiring people that are willing to learn and then invest the time and money to make it happen.

Hire assessing values rather than a specific skill set is one of the best things I have learned to do in the past two years. Hire people that fits in the culture and values of your company and let them learn. 

Learning is the easy part most of the time.

* * *

_PS: The cloud also has downsides \(what does not\) but we are not here to talk about that._
